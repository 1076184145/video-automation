"""Tests for segment-parallel final rendering and concat merging."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from video_automation import render
from video_automation.config import Settings


def _settings(**overrides: object) -> Settings:
    values = {
        "render_video_encoder": "libx264",
        "render_output_fps": 30,
        "render_segment_parallel_enabled": True,
        "render_segment_workers": 2,
        "export_platforms": ("douyin",),
        "web_preview_enabled": False,
        "bgm_path": None,
    }
    values.update(overrides)
    return replace(Settings.load(), **values)


def _clip(start: float, end: float) -> dict[str, float]:
    return {"start": start, "end": end, "keep": True}


class BuildSegmentRenderCommandTests(unittest.TestCase):
    def test_input_seek_and_duration_are_exact(self) -> None:
        settings = _settings()
        command = render.build_segment_render_command(
            settings,
            Path("source.mp4"),
            _clip(12.5, 42.25),
            Path("seg.mp4"),
            post_filters=[],
        )
        self.assertIn("-ss", command)
        self.assertEqual(command[command.index("-ss") + 1], "12.500000")
        self.assertEqual(command[command.index("-t") + 1], "29.750000")
        self.assertIn("fps=30", command[command.index("-vf") + 1])
        self.assertIn("aresample=async=1:first_pts=0", command)
        self.assertEqual(command[-1], "seg.mp4")

    def test_fps_zero_preserves_source_timing(self) -> None:
        settings = _settings(render_output_fps=0)
        command = render.build_segment_render_command(
            settings, Path("s.mp4"), _clip(0, 5), Path("o.mp4"), post_filters=[]
        )
        self.assertNotIn("-vf", command)

    def test_vertical_crop_filter_applied_per_segment(self) -> None:
        settings = _settings()
        command = render.build_segment_render_command(
            settings,
            Path("s.mp4"),
            _clip(0, 5),
            Path("o.mp4"),
            post_filters=["crop=1080:1920:420:0,scale=1080:1920"],
        )
        self.assertIn("crop=1080:1920:420:0,scale=1080:1920", command[command.index("-vf") + 1])


class BuildConcatCommandTests(unittest.TestCase):
    def test_concat_list_uses_forward_slashes_and_stream_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            segments = [Path(temp_dir) / f"segment_{i:03d}.mp4" for i in range(2)]
            output = Path(temp_dir) / "merged.mp4"
            command = render.build_concat_command(_settings(), segments, output)
            list_path = Path(command[command.index("-i") + 1])
            self.assertTrue(list_path.is_file())
            content = list_path.read_text(encoding="utf-8")
            self.assertIn("file '", content)
            self.assertNotIn("\\", content)
            self.assertEqual(content.count("file '"), 2)
            self.assertIn("-c", command)
            self.assertEqual(command[command.index("-c") + 1], "copy")


class BuildSegmentFinishCommandTests(unittest.TestCase):
    def test_subtitles_only_reencodes_video_and_copies_audio(self) -> None:
        settings = _settings()
        command = render.build_segment_finish_command(
            settings,
            Path("merged.mp4"),
            Path("out.mp4"),
            subtitle_path=Path("subs.ass"),
            bgm_path=None,
            duration=60.0,
        )
        joined = " ".join(command)
        self.assertIn("subtitles=", joined)
        self.assertIn("-c:a copy", joined)
        self.assertNotIn("-b:a", joined)
        self.assertIn("libx264", joined)

    def test_bgm_only_copies_video_and_remixes_audio(self) -> None:
        settings = _settings(bgm_path=Path("bgm.mp3"))
        command = render.build_segment_finish_command(
            settings,
            Path("merged.mp4"),
            Path("out.mp4"),
            subtitle_path=None,
            bgm_path=Path("bgm.mp3"),
            duration=60.0,
        )
        joined = " ".join(command)
        self.assertIn("-c:v copy", joined)
        self.assertIn("amix=inputs=2", joined)
        self.assertIn("-stream_loop", joined)

    def test_both_reencode_video_and_remix(self) -> None:
        settings = _settings(bgm_path=Path("bgm.mp3"))
        command = render.build_segment_finish_command(
            settings,
            Path("merged.mp4"),
            Path("out.mp4"),
            subtitle_path=Path("subs.ass"),
            bgm_path=Path("bgm.mp3"),
            duration=60.0,
        )
        joined = " ".join(command)
        self.assertIn("subtitles=", joined)
        self.assertIn("amix=inputs=2", joined)
        self.assertNotIn("-c:v copy", joined)


class SegmentedFinalRenderTests(unittest.TestCase):
    def _fake_runner(self, settings: Settings, command: list[str], **_: object) -> object:
        output = Path(command[-1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"mp4")
        return SimpleNamespace(returncode=0, stderr="")

    def _job_dir(self, temp_dir: str) -> Path:
        job_dir = Path(temp_dir)
        (job_dir / "cuts.json").write_text(
            json.dumps({
                "clips": [
                    {"start": 0.0, "end": 5.0, "keep": True},
                    {"start": 60.0, "end": 75.5, "keep": True},
                    {"start": 120.0, "end": 130.0, "keep": True},
                ]
            }),
            encoding="utf-8",
        )
        return job_dir

    def test_renders_segments_concat_directly_without_finish(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = self._job_dir(temp_dir)
            source = job_dir / "source.mp4"
            source.write_bytes(b"src")
            settings = _settings()

            with (
                patch.object(render, "_run_ffmpeg_with_resource_gate", side_effect=self._fake_runner),
                patch.object(
                    render,
                    "run_ffmpeg_with_progress",
                    side_effect=lambda command, **_: (
                        Path(str(command[-1])).write_bytes(b"mp4"),
                        SimpleNamespace(returncode=0, stderr="", stdout=""),
                    )[1],
                ),
                patch.object(render, "_valid_media_output", return_value=True),
                patch.object(render, "_refresh_web_preview") as refresh,
            ):
                output = render.render_final_video(settings, job_dir, source)

            self.assertTrue(output.is_file())
            self.assertFalse((job_dir / ".render_segments").exists())
            refresh.assert_called()
            preview = json.loads((job_dir / "final_render_preview.json").read_text("utf-8"))
            self.assertEqual(preview["mode"], "segmented")
            self.assertEqual(preview["segment_workers"], 2)
            self.assertEqual(len(preview["segment_commands"]), 3)
            self.assertEqual(preview["finish_command"], [])
            self.assertEqual(preview["command"], preview["concat_command"])

    def test_subtitles_and_bgm_run_finish_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = self._job_dir(temp_dir)
            source = job_dir / "source.mp4"
            source.write_bytes(b"src")
            (job_dir / "subtitles_clipped.ass").write_text("[Script Info]", encoding="utf-8")
            bgm = job_dir / "bgm.mp3"
            bgm.write_bytes(b"bgm")
            settings = _settings(bgm_path=bgm)

            with (
                patch.object(render, "_run_ffmpeg_with_resource_gate", side_effect=self._fake_runner),
                patch.object(render, "run_ffmpeg_with_progress", side_effect=lambda command, **_: SimpleNamespace(
                    returncode=0, stderr="", stdout=""
                )),
                patch.object(render, "_valid_media_output", return_value=True),
                patch.object(render, "_refresh_web_preview"),
            ):
                output = render.render_final_video(
                    settings, job_dir, source, burn_subtitles=True
                )

            self.assertTrue(output.is_file())
            preview = json.loads((job_dir / "final_render_preview.json").read_text("utf-8"))
            self.assertEqual(preview["mode"], "segmented")
            self.assertTrue(preview["finish_command"])
            self.assertIn("subtitles=", " ".join(preview["finish_command"]))
            self.assertIn("amix", " ".join(preview["finish_command"]))

    def test_segment_failure_cancels_pending_and_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = self._job_dir(temp_dir)
            source = job_dir / "source.mp4"
            source.write_bytes(b"src")
            settings = _settings(render_segment_workers=1)

            def failing_runner(settings: Settings, command: list[str], **_: object) -> object:
                output = Path(command[-1])
                if "segment_001" in output.name:
                    return SimpleNamespace(returncode=1, stderr="boom")
                output.write_bytes(b"mp4")
                return SimpleNamespace(returncode=0, stderr="")

            with (
                patch.object(render, "_run_ffmpeg_with_resource_gate", side_effect=failing_runner),
                patch.object(render, "_valid_media_output", return_value=True),
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    render.render_final_video(settings, job_dir, source)

            self.assertIn("segment 001", str(ctx.exception))
            self.assertFalse((job_dir / "final.mp4").exists())

    def test_disabled_flag_uses_monolithic_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = self._job_dir(temp_dir)
            source = job_dir / "source.mp4"
            source.write_bytes(b"src")
            settings = _settings(render_segment_parallel_enabled=False)

            with (
                patch.object(render, "_run_ffmpeg_with_resource_gate", side_effect=self._fake_runner) as gate,
                patch.object(render, "_valid_media_output", return_value=True),
                patch.object(render, "_refresh_web_preview"),
            ):
                render.render_final_video(settings, job_dir, source)

            gate.assert_called_once()
            preview = json.loads((job_dir / "final_render_preview.json").read_text("utf-8"))
            self.assertNotEqual(preview.get("mode"), "segmented")

    def test_single_clip_falls_back_to_monolithic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            (job_dir / "cuts.json").write_text(
                json.dumps({"clips": [{"start": 0.0, "end": 10.0, "keep": True}]}),
                encoding="utf-8",
            )
            source = job_dir / "source.mp4"
            source.write_bytes(b"src")
            settings = _settings()

            with (
                patch.object(render, "_run_ffmpeg_with_resource_gate", side_effect=self._fake_runner) as gate,
                patch.object(render, "_valid_media_output", return_value=True),
                patch.object(render, "_refresh_web_preview"),
            ):
                render.render_final_video(settings, job_dir, source)

            gate.assert_called_once()
            preview = json.loads((job_dir / "final_render_preview.json").read_text("utf-8"))
            self.assertNotEqual(preview.get("mode"), "segmented")


if __name__ == "__main__":
    unittest.main()
