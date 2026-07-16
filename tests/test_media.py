from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import wave
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from video_automation import api, media, worker
from video_automation.config import Settings
from video_automation.jobs import Job
from video_automation.media import (
    detect_visual_events,
    extract_audio_outputs,
    parse_freeze_output,
    parse_scene_output,
    parse_silence_output,
)


class MediaParserTests(unittest.TestCase):
    def test_run_command_bounds_captured_output_before_returning(self) -> None:
        with patch.object(media, "MAX_COMMAND_OUTPUT_BYTES", 1024):
            result = media.run_command(
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.stdout.write('o' * 2048); sys.stderr.write('e' * 2048)",
                ],
                timeout=5,
            )

        self.assertEqual(result.returncode, 0)
        self.assertLessEqual(len(result.stdout.encode("utf-8")), 1024)
        self.assertLessEqual(len(result.stderr.encode("utf-8")), 1024)

    def test_parse_silence_output_pairs_start_and_end(self) -> None:
        text = """
        [silencedetect @ 000] silence_start: 1.234
        [silencedetect @ 000] silence_end: 2.345 | silence_duration: 1.111
        [silencedetect @ 000] silence_end: 5.000 | silence_duration: 0.500
        """
        self.assertEqual(
            parse_silence_output(text),
            [
                {"start": 1.234, "end": 2.345, "duration": 1.111},
                {"start": 4.5, "end": 5.0, "duration": 0.5},
            ],
        )

    def test_parse_freeze_output_pairs_start_and_end(self) -> None:
        text = """
        freeze_start: 10.0
        freeze_end: 13.25 | freeze_duration: 3.25
        """
        self.assertEqual(parse_freeze_output(text), [{"start": 10.0, "end": 13.25, "duration": 3.25}])

    def test_parse_scene_output_deduplicates_pts_time(self) -> None:
        text = """
        [Parsed_showinfo_1 @ 000] n:1 pts:0 pts_time:12.345 pos:0
        [Parsed_showinfo_1 @ 000] n:2 pts:0 pts_time:12.345 pos:0
        [Parsed_showinfo_1 @ 000] n:3 pts:0 pts_time:18.9 pos:0
        """
        self.assertEqual(
            parse_scene_output(text),
            [
                {"time": 12.345, "reason": "scene_change"},
                {"time": 18.9, "reason": "scene_change"},
            ],
        )

    def test_parse_scene_output_rejects_excessive_event_count(self) -> None:
        text = "\n".join(f"showinfo pts_time:{index}.0" for index in range(4))
        with patch.object(media, "MAX_VISUAL_EVENTS", 3):
            with self.assertRaisesRegex(RuntimeError, "visual event limit exceeded"):
                parse_scene_output(text)


class AudioExtractionTests(unittest.TestCase):
    def test_severe_source_corruption_stops_pipeline_before_expensive_stages(self) -> None:
        settings = SimpleNamespace(source_integrity_scan_max_errors=40)
        with self.assertRaisesRegex(RuntimeError, "70358 decode errors"):
            worker._raise_for_severe_source_corruption(  # type: ignore[attr-defined]
                settings,  # type: ignore[arg-type]
                {"status": "corrupt", "error_count": 70358},
            )

        worker._raise_for_severe_source_corruption(  # type: ignore[attr-defined]
            settings,  # type: ignore[arg-type]
            {"status": "corrupt", "error_count": 3},
        )

    def test_joint_extraction_can_scan_video_integrity_in_same_ffmpeg_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.mp4"
            wav_path = root / "audio.wav"
            flac_path = root / "audio_hq.flac"
            integrity_path = root / "corrupt.json"
            settings = SimpleNamespace(
                ffmpeg_path=Path("ffmpeg"),
                transcribe_audio_filter="",
                source_integrity_scan_enabled=True,
                source_integrity_scan_timeout_multiplier=3.0,
                source_integrity_scan_max_errors=40,
            )
            commands: list[list[str]] = []

            def fake_run(command: list[str], *, timeout: int | None = None):
                commands.append(command)
                Path(command[command.index("pcm_s16le") + 1]).write_bytes(b"wav")
                Path(command[command.index("flac") + 1]).write_bytes(b"flac")
                return SimpleNamespace(
                    returncode=0,
                    stdout="out_time=00:00:12.000000\nprogress=end\n",
                    stderr="[h264] error while decoding MB 12 4\n",
                )

            with patch.object(media, "run_command", side_effect=fake_run):
                extract_audio_outputs(
                    settings,  # type: ignore[arg-type]
                    source,
                    wav_path,
                    flac_path,
                    integrity_output_path=integrity_path,
                    duration=120.0,
                )

            self.assertEqual(len(commands), 1)
            self.assertEqual(commands[0].count("-i"), 1)
            self.assertEqual(commands[0].count("-map"), 3)
            self.assertIn(os.devnull, commands[0])
            payload = json.loads(integrity_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "corrupt")
            self.assertEqual(payload["scan_mode"], "combined_full_decode")
            self.assertIsNone(payload["first_error_at_seconds"])
            self.assertEqual(payload["scan_completed_at_seconds"], 12.0)

    def test_joint_extraction_uses_one_ffmpeg_process_for_wav_and_flac(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.mp4"
            wav_path = root / "audio.wav"
            flac_path = root / "audio_hq.flac"
            settings = SimpleNamespace(
                ffmpeg_path=Path("ffmpeg"),
                transcribe_audio_filter="highpass=f=120",
            )
            commands: list[list[str]] = []

            def fake_run(command: list[str], *, timeout: int | None = None):
                commands.append(command)
                Path(command[command.index("pcm_s16le") + 1]).write_bytes(b"wav")
                Path(command[command.index("flac") + 1]).write_bytes(b"flac")
                return SimpleNamespace(returncode=0, stderr="")

            with patch.object(media, "run_command", side_effect=fake_run):
                extract_audio_outputs(settings, source, wav_path, flac_path)  # type: ignore[arg-type]

            self.assertEqual(len(commands), 1)
            self.assertEqual(commands[0].count("-i"), 1)
            self.assertEqual(commands[0].count("-map"), 2)
            self.assertIn("highpass=f=120", commands[0])
            self.assertEqual(wav_path.read_bytes(), b"wav")
            self.assertEqual(flac_path.read_bytes(), b"flac")

    def test_joint_extraction_reuses_complete_outputs_without_ffmpeg(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wav_path = root / "audio.wav"
            flac_path = root / "audio_hq.flac"
            wav_path.write_bytes(b"existing wav")
            flac_path.write_bytes(b"existing flac")
            settings = SimpleNamespace(ffmpeg_path=Path("ffmpeg"), transcribe_audio_filter="")

            with patch.object(media, "run_command") as run:
                extract_audio_outputs(settings, root / "source.mp4", wav_path, flac_path)  # type: ignore[arg-type]

            run.assert_not_called()

    def test_joint_extraction_only_regenerates_missing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wav_path = root / "audio.wav"
            flac_path = root / "audio_hq.flac"
            wav_path.write_bytes(b"existing wav")
            settings = SimpleNamespace(ffmpeg_path=Path("ffmpeg"), transcribe_audio_filter="")
            commands: list[list[str]] = []

            def fake_run(command: list[str], *, timeout: int | None = None):
                commands.append(command)
                Path(command[command.index("flac") + 1]).write_bytes(b"new flac")
                return SimpleNamespace(returncode=0, stderr="")

            with patch.object(media, "run_command", side_effect=fake_run):
                extract_audio_outputs(settings, root / "source.mp4", wav_path, flac_path)  # type: ignore[arg-type]

            self.assertEqual(len(commands), 1)
            self.assertNotIn("pcm_s16le", commands[0])
            self.assertEqual(wav_path.read_bytes(), b"existing wav")
            self.assertEqual(flac_path.read_bytes(), b"new flac")

    def test_joint_extraction_cleans_temporary_outputs_after_ffmpeg_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wav_path = root / "audio.wav"
            flac_path = root / "audio_hq.flac"
            settings = SimpleNamespace(ffmpeg_path=Path("ffmpeg"), transcribe_audio_filter="")

            def fake_run(command: list[str], *, timeout: int | None = None):
                for suffix in ("pcm_s16le", "flac"):
                    Path(command[command.index(suffix) + 1]).write_bytes(b"partial")
                return SimpleNamespace(returncode=1, stderr="decode failed")

            with patch.object(media, "run_command", side_effect=fake_run):
                with self.assertRaisesRegex(RuntimeError, "decode failed"):
                    extract_audio_outputs(settings, root / "source.mp4", wav_path, flac_path)  # type: ignore[arg-type]

            self.assertFalse(wav_path.exists())
            self.assertFalse(flac_path.exists())
            self.assertFalse(media._temp_media_path(wav_path).exists())
            self.assertFalse(media._temp_media_path(flac_path).exists())

    def test_pipeline_audio_stage_uses_joint_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = SimpleNamespace(source_integrity_scan_enabled=False, high_quality_audio_enabled=True)
            job = SimpleNamespace(
                status="pending",
                source_path=root / "source.mp4",
                job_dir=root / "job",
                set_status=lambda _status: None,
                fail=lambda _error: None,
            )

            def run_audio_stage(_progress, _job, stages, context):
                next(stage for stage in stages if stage.name == "extract_audio").run(context)

            with (
                patch.object(worker, "configure_job_logger", return_value=SimpleNamespace(info=lambda *_args: None, exception=lambda *_args: None)),
                patch.object(worker, "run_pipeline", side_effect=run_audio_stage),
                patch.object(worker, "extract_audio_outputs", create=True) as joint,
                patch.object(worker, "generate_waveform"),
            ):
                worker.process_job(
                    settings,  # type: ignore[arg-type]
                    job,  # type: ignore[arg-type]
                    force=False,
                    detect_silence_enabled=False,
                    detect_freeze_enabled=False,
                    detect_scenes_enabled=False,
                    render_review_enabled=False,
                    render_final_enabled=False,
                    vertical_enabled=False,
                    burn_subtitles_enabled=False,
                    plan_crop_enabled=False,
                    plan_uvr_enabled=False,
                    skip_transcribe=False,
                    progress_enabled=False,
                )

            joint.assert_called_once_with(
                settings,
                job.source_path,
                job.job_dir / "audio.wav",
                job.job_dir / "audio_hq.flac",
                force=False,
            )

    def test_pipeline_audio_stage_skips_high_quality_audio_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = SimpleNamespace(source_integrity_scan_enabled=False, high_quality_audio_enabled=False)
            job = SimpleNamespace(
                status="pending",
                source_path=root / "source.mp4",
                job_dir=root / "job",
                set_status=lambda _status: None,
                fail=lambda _error: None,
            )

            def run_audio_stage(_progress, _job, stages, context):
                next(stage for stage in stages if stage.name == "extract_audio").run(context)

            with (
                patch.object(worker, "configure_job_logger", return_value=SimpleNamespace(info=lambda *_args: None, exception=lambda *_args: None)),
                patch.object(worker, "run_pipeline", side_effect=run_audio_stage),
                patch.object(worker, "extract_audio_outputs", create=True) as joint,
                patch.object(worker, "generate_waveform"),
            ):
                worker.process_job(
                    settings,  # type: ignore[arg-type]
                    job,  # type: ignore[arg-type]
                    force=False,
                    detect_silence_enabled=False,
                    detect_freeze_enabled=False,
                    detect_scenes_enabled=False,
                    render_review_enabled=False,
                    render_final_enabled=False,
                    vertical_enabled=False,
                    burn_subtitles_enabled=False,
                    plan_crop_enabled=False,
                    plan_uvr_enabled=False,
                    skip_transcribe=False,
                    progress_enabled=False,
                )

            joint.assert_called_once_with(
                settings,
                job.source_path,
                job.job_dir / "audio.wav",
                None,
                force=False,
            )

    def test_pipeline_audio_stage_keeps_high_quality_audio_for_uvr(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = SimpleNamespace(source_integrity_scan_enabled=False, high_quality_audio_enabled=False)
            job = SimpleNamespace(
                status="pending",
                source_path=root / "source.mp4",
                job_dir=root / "job",
                set_status=lambda _status: None,
                fail=lambda _error: None,
            )

            def run_audio_stage(_progress, _job, stages, context):
                next(stage for stage in stages if stage.name == "extract_audio").run(context)

            with (
                patch.object(worker, "configure_job_logger", return_value=SimpleNamespace(info=lambda *_args: None, exception=lambda *_args: None)),
                patch.object(worker, "run_pipeline", side_effect=run_audio_stage),
                patch.object(worker, "extract_audio_outputs", create=True) as joint,
                patch.object(worker, "generate_waveform"),
            ):
                worker.process_job(
                    settings,  # type: ignore[arg-type]
                    job,  # type: ignore[arg-type]
                    force=False,
                    detect_silence_enabled=False,
                    detect_freeze_enabled=False,
                    detect_scenes_enabled=False,
                    render_review_enabled=False,
                    render_final_enabled=False,
                    vertical_enabled=False,
                    burn_subtitles_enabled=False,
                    plan_crop_enabled=False,
                    plan_uvr_enabled=True,
                    skip_transcribe=False,
                    progress_enabled=False,
                )

            joint.assert_called_once_with(
                settings,
                job.source_path,
                job.job_dir / "audio.wav",
                job.job_dir / "audio_hq.flac",
                force=False,
            )

    def test_pipeline_integrity_scan_and_audio_extraction_share_media_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = SimpleNamespace(
                source_integrity_scan_enabled=True,
                source_integrity_scan_timeout_multiplier=3.0,
                source_integrity_scan_max_errors=40,
                high_quality_audio_enabled=True,
            )
            job = SimpleNamespace(
                status="pending",
                source_path=root / "source.mp4",
                job_dir=root / "job",
                set_status=lambda _status: None,
                fail=lambda _error: None,
            )

            def run_prepare_stages(_progress, _job, stages, context):
                context["manifest"] = {"duration_seconds": 120.0, "video_stream_count": 1}
                next(stage for stage in stages if stage.name == "detect_corruption").run(context)
                next(stage for stage in stages if stage.name == "extract_audio").run(context)

            with (
                patch.object(worker, "configure_job_logger", return_value=SimpleNamespace(info=lambda *_args: None, exception=lambda *_args: None)),
                patch.object(worker, "run_pipeline", side_effect=run_prepare_stages),
                patch.object(worker, "extract_audio_outputs", create=True) as joint,
                patch.object(worker, "generate_waveform"),
            ):
                worker.process_job(
                    settings,  # type: ignore[arg-type]
                    job,  # type: ignore[arg-type]
                    force=False,
                    detect_silence_enabled=False,
                    detect_freeze_enabled=False,
                    detect_scenes_enabled=False,
                    render_review_enabled=False,
                    render_final_enabled=False,
                    vertical_enabled=False,
                    burn_subtitles_enabled=False,
                    plan_crop_enabled=False,
                    plan_uvr_enabled=False,
                    skip_transcribe=False,
                    progress_enabled=False,
                )

            joint.assert_called_once_with(
                settings,
                job.source_path,
                job.job_dir / "audio.wav",
                job.job_dir / "audio_hq.flac",
                integrity_output_path=job.job_dir / "corrupt.json",
                duration=120.0,
                force=False,
            )

    def test_rerun_audio_stage_uses_joint_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job_dir = root / "jobs" / "job"
            job_dir.mkdir(parents=True)
            job = Job(source_path=root / "source.mp4", job_dir=job_dir, status="queued")
            settings = replace(Settings.load(), root=root, jobs_dir=root / "jobs")

            with (
                patch.object(worker, "extract_audio_outputs", create=True) as joint,
                patch.object(worker, "generate_waveform"),
            ):
                api._run_single_stage(settings, job, "extract_audio", {})  # type: ignore[arg-type]

            joint.assert_called_once_with(
                settings,
                job.source_path,
                job.job_dir / "audio.wav",
                job.job_dir / "audio_hq.flac",
                force=True,
            )

    def test_rerun_final_stage_does_not_hide_web_preview_work_inside_final_render(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job_dir = root / "jobs" / "job"
            job_dir.mkdir(parents=True)
            job = Job(source_path=root / "source.mp4", job_dir=job_dir, status="queued")
            settings = replace(Settings.load(), root=root, jobs_dir=root / "jobs")

            with patch.object(worker, "render_final_video") as render_final:
                api._run_single_stage(settings, job, "render_final", {})  # type: ignore[arg-type]

            self.assertFalse(render_final.call_args.kwargs["refresh_web_preview"])

    def test_rerun_web_preview_stage_renders_from_existing_final(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job_dir = root / "jobs" / "job"
            job_dir.mkdir(parents=True)
            (job_dir / "final.mp4").write_bytes(b"final")
            job = Job(source_path=root / "source.mp4", job_dir=job_dir, status="queued")
            settings = replace(Settings.load(), root=root, jobs_dir=root / "jobs")

            with patch.object(worker, "render_web_preview") as render_preview:
                api._run_single_stage(settings, job, "render_web_preview", {})  # type: ignore[arg-type]

            self.assertEqual(render_preview.call_args.kwargs["source_path"], job_dir / "final.mp4")
            self.assertTrue(render_preview.call_args.kwargs["force"])


class WaveformFallbackTests(unittest.TestCase):
    def test_waveform_fallback_uses_native_payload_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "audio.wav"
            _write_test_wav(audio_path)
            native_payload = {
                "status": "ready",
                "source": "rust_wave_fallback",
                "sample_rate": 100,
                "channels": 1,
                "bits": 16,
                "pixels_per_second": 20,
                "duration": 0.1,
                "data": [-1, 1],
            }

            with patch.object(media.native_waveform, "generate_waveform", return_value=native_payload) as native:
                payload = media._generate_waveform_fallback(
                    audio_path,
                    reason="audiowaveform_not_configured",
                    tool="audiowaveform",
                    native_enabled=True,
                )

            native.assert_called_once_with(audio_path, pixels_per_second=20)
            self.assertEqual(payload["source"], "rust_wave_fallback")
            self.assertEqual(payload["fallback_reason"], "audiowaveform_not_configured")
            self.assertEqual(payload["tool"], "audiowaveform")
            self.assertEqual(payload["data"], [-1, 1])

    def test_waveform_fallback_skips_native_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "audio.wav"
            _write_test_wav(audio_path)

            with patch.object(media.native_waveform, "generate_waveform") as native:
                payload = media._generate_waveform_fallback(
                    audio_path,
                    reason="audiowaveform_not_configured",
                    tool="audiowaveform",
                    native_enabled=False,
                )

            native.assert_not_called()
            self.assertEqual(payload["source"], "python_wave_fallback")
            self.assertEqual(payload["status"], "ready")
            self.assertTrue(payload["data"])

    def test_waveform_fallback_recovers_from_native_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "audio.wav"
            _write_test_wav(audio_path)

            with patch.object(media.native_waveform, "generate_waveform", side_effect=RuntimeError("native failed")):
                with self.assertLogs("video_automation.media", level="WARNING") as logs:
                    payload = media._generate_waveform_fallback(
                        audio_path,
                        reason="audiowaveform_failed",
                        tool="audiowaveform",
                        error="cli failed",
                        native_enabled=True,
                    )

            self.assertEqual(payload["source"], "python_wave_fallback")
            self.assertEqual(payload["error"], "cli failed")
            self.assertTrue(payload["data"])
            self.assertIn("Native waveform fallback failed", "\n".join(logs.output))

    def test_waveform_fallback_skips_native_for_large_wav(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "audio.wav"
            _write_test_wav(audio_path)

            with (
                patch.object(Path, "stat", return_value=SimpleNamespace(st_size=media.NATIVE_WAVEFORM_MAX_BYTES + 1)),
                patch.object(media.native_waveform, "generate_waveform") as native,
                self.assertLogs("video_automation.media", level="INFO") as logs,
            ):
                payload = media._generate_waveform_fallback(
                    audio_path,
                    reason="audiowaveform_not_configured",
                    tool="audiowaveform",
                    native_enabled=True,
                )

            native.assert_not_called()
            self.assertEqual(payload["source"], "python_wave_fallback")
            self.assertIn("Skipping native waveform fallback", "\n".join(logs.output))

    def test_installed_native_waveform_matches_python_fallback(self) -> None:
        try:
            import video_automation_native  # noqa: F401
        except ImportError:
            self.skipTest("optional video_automation_native extension is not installed")

        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = Path(temp_dir) / "audio.wav"
            _write_test_wav(audio_path)

            native_payload = media.native_waveform.generate_waveform(audio_path, pixels_per_second=20)
            python_payload = media._generate_waveform_fallback(
                audio_path,
                reason="audiowaveform_not_configured",
                tool="audiowaveform",
                native_enabled=False,
            )

        self.assertEqual(native_payload["source"], "rust_wave_fallback")
        for key in ("sample_rate", "channels", "bits", "pixels_per_second", "duration", "data"):
            self.assertEqual(native_payload[key], python_payload[key])


class VisualDetectionTests(unittest.TestCase):
    def test_joint_visual_detection_uses_one_decode_for_freeze_and_scenes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            freeze_path = root / "freeze.json"
            scene_path = root / "scene.json"
            settings = SimpleNamespace(
                ffmpeg_path=Path("ffmpeg"),
                freeze_noise_db=-50.0,
                freeze_min_duration_seconds=2.0,
                scene_threshold=0.3,
                visual_detect_keyframes_only=False,
                visual_detect_fps=2.0,
                visual_detect_width=640,
            )
            commands: list[list[str]] = []

            def fake_run(command: list[str], *, timeout: int | None = None):
                commands.append(command)
                return SimpleNamespace(
                    returncode=0,
                    stdout="",
                    stderr=(
                        "freeze_start: 10.0\n"
                        "freeze_end: 13.0 | freeze_duration: 3.0\n"
                        "[Parsed_showinfo_4] pts_time:18.5\n"
                    ),
                )

            with patch.object(media, "run_command", side_effect=fake_run):
                freeze_payload, scene_payload = detect_visual_events(
                    settings,  # type: ignore[arg-type]
                    root / "source.mp4",
                    60.0,
                    freeze_path,
                    scene_path,
                )

            self.assertEqual(len(commands), 1)
            self.assertEqual(commands[0].count("-i"), 1)
            self.assertIn("-filter_complex", commands[0])
            self.assertEqual(commands[0].count("-map"), 1)
            self.assertIn("nullsink", commands[0][commands[0].index("-filter_complex") + 1])
            self.assertEqual(freeze_payload["freezes"], [{"start": 10.0, "end": 13.0, "duration": 3.0}])
            self.assertEqual(scene_payload["scenes"], [{"time": 18.5, "reason": "scene_change"}])
            self.assertEqual(json.loads(freeze_path.read_text(encoding="utf-8"))["freezes"], freeze_payload["freezes"])
            self.assertEqual(json.loads(scene_path.read_text(encoding="utf-8"))["scenes"], scene_payload["scenes"])

    def test_pipeline_freeze_and_scene_stages_share_visual_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = SimpleNamespace(source_integrity_scan_enabled=False)
            job = SimpleNamespace(
                status="pending",
                source_path=root / "source.mp4",
                job_dir=root / "job",
                set_status=lambda _status: None,
                fail=lambda _error: None,
            )

            def run_visual_stages(_progress, _job, stages, context):
                context["manifest"] = {"duration_seconds": 120.0, "video_stream_count": 1}
                next(stage for stage in stages if stage.name == "detect_freeze").run(context)
                next(stage for stage in stages if stage.name == "detect_scenes").run(context)

            with (
                patch.object(worker, "configure_job_logger", return_value=SimpleNamespace(info=lambda *_args: None, exception=lambda *_args: None)),
                patch.object(worker, "run_pipeline", side_effect=run_visual_stages),
                patch.object(worker, "detect_visual_events", create=True) as joint,
            ):
                worker.process_job(
                    settings,  # type: ignore[arg-type]
                    job,  # type: ignore[arg-type]
                    force=False,
                    detect_silence_enabled=False,
                    detect_freeze_enabled=True,
                    detect_scenes_enabled=True,
                    render_review_enabled=False,
                    render_final_enabled=False,
                    vertical_enabled=False,
                    burn_subtitles_enabled=False,
                    plan_crop_enabled=False,
                    plan_uvr_enabled=False,
                    skip_transcribe=False,
                    progress_enabled=False,
                )

            joint.assert_called_once_with(
                settings,
                job.source_path,
                120.0,
                job.job_dir / "freeze.json",
                job.job_dir / "scene.json",
                force=False,
            )


def _write_test_wav(path: Path) -> None:
    samples = [-32768, -1000, 0, 1000, 32767, -2000, -1000, 0, 1000, 2000]
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(100)
        handle.writeframes(b"".join(int(sample).to_bytes(2, "little", signed=True) for sample in samples))


if __name__ == "__main__":
    unittest.main()
