"""Tests for per-platform multi-aspect variant rendering."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from video_automation import render
from video_automation.config import Settings
from video_automation.pipeline_spec import PIPELINE_STAGE_SPECS


def _settings(**overrides: object) -> Settings:
    values = {
        "export_platforms": ("douyin", "bilibili", "youtube_shorts"),
        "platform_variants_enabled": True,
        "render_video_encoder": "libx264",
        "ass_preset": "classic",
        "vertical_mode": "crop",
    }
    values.update(overrides)
    return replace(Settings.load(), **values)


class PlatformVariantTargetsTests(unittest.TestCase):
    def test_vertical_primary_renders_landscape_and_other_verticals(self) -> None:
        targets = render.platform_variant_targets(_settings(), primary_vertical=True)
        self.assertEqual(
            targets,
            [("bilibili", False), ("youtube_shorts", True)],
        )

    def test_landscape_primary_renders_vertical_platforms(self) -> None:
        settings = _settings(export_platforms=("bilibili", "douyin"))
        targets = render.platform_variant_targets(settings, primary_vertical=False)
        self.assertEqual(targets, [("douyin", True)])

    def test_primary_platform_matching_aspect_is_skipped(self) -> None:
        settings = _settings(export_platforms=("douyin",))
        self.assertEqual(render.platform_variant_targets(settings, primary_vertical=True), [])

    def test_unknown_platforms_are_ignored(self) -> None:
        settings = _settings(export_platforms=("douyin", "podcast"))
        targets = render.platform_variant_targets(settings, primary_vertical=False)
        self.assertEqual(targets, [("douyin", True)])

    def test_duplicate_platforms_deduped(self) -> None:
        settings = _settings(export_platforms=("douyin", "douyin", "bilibili", "bilibili"))
        targets = render.platform_variant_targets(settings, primary_vertical=True)
        self.assertEqual(targets, [("bilibili", False)])


class RenderPlatformVariantsTests(unittest.TestCase):
    def test_renders_each_target_and_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            settings = _settings()

            def fake_render_final(
                platform_settings: Settings,
                job_dir: Path,
                source_path: Path,
                *,
                force: bool,
                vertical: bool,
                burn_subtitles: bool,
                subtitle_filename: str | None,
                output_filename: str,
                **_: object,
            ) -> Path:
                output = job_dir / output_filename
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"mp4")
                fake_render_final.calls.append({
                    "platform": platform_settings.export_platforms,
                    "ass_preset": platform_settings.ass_preset,
                    "vertical": vertical,
                    "subtitle_filename": subtitle_filename,
                    "output_filename": output_filename,
                })
                return output

            fake_render_final.calls = []  # type: ignore[attr-defined]
            source = job_dir / "source.mp4"
            source.write_bytes(b"src")

            with patch.object(render, "render_final_video", side_effect=fake_render_final):
                manifest = render.render_platform_variants(
                    settings,
                    job_dir,
                    source,
                    primary_vertical=True,
                    burn_subtitles=True,
                )

            self.assertEqual(manifest["status"], "ready")
            self.assertEqual(sorted(manifest["variants"]), ["bilibili", "youtube_shorts"])
            calls = fake_render_final.calls  # type: ignore[attr-defined]
            self.assertEqual([call["platform"] for call in calls], [("bilibili",), ("youtube_shorts",)])
            self.assertEqual([call["vertical"] for call in calls], [False, True])
            self.assertEqual(
                [call["subtitle_filename"] for call in calls],
                ["subtitles_clipped_bilibili.ass", "subtitles_clipped_youtube_shorts.ass"],
            )
            self.assertEqual(
                [call["output_filename"] for call in calls],
                ["variants/bilibili.mp4", "variants/youtube_shorts.mp4"],
            )
            self.assertEqual(
                [call["ass_preset"] for call in calls],
                ["bilibili", "douyin"],
            )
            stored = json.loads((job_dir / "variants" / "platform_variants.json").read_text("utf-8"))
            self.assertEqual(stored["status"], "ready")
            self.assertEqual(stored["variants"]["bilibili"]["resolution"], "1920x1080")

    def test_no_targets_writes_skipped_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            settings = _settings(export_platforms=("douyin",))
            source = job_dir / "source.mp4"
            source.write_bytes(b"src")

            manifest = render.render_platform_variants(
                settings, job_dir, source, primary_vertical=True
            )

            self.assertEqual(manifest["status"], "skipped")
            self.assertTrue((job_dir / "variants" / "platform_variants.json").is_file())


class PipelineWiringTests(unittest.TestCase):
    def test_stage_registered_after_final_render(self) -> None:
        spec = PIPELINE_STAGE_SPECS["render_platform_variants"]
        self.assertEqual(set(spec.dependencies), {"render_final"})
        self.assertIn("render_final", spec.rerun_dependencies)
        self.assertEqual(PIPELINE_STAGE_SPECS["render_web_preview"].dependencies, frozenset({"render_final"}))

    def test_stage_order_final_before_variants_before_web_preview(self) -> None:
        names = list(PIPELINE_STAGE_SPECS)
        self.assertLess(names.index("render_final"), names.index("render_platform_variants"))
        self.assertLess(names.index("render_platform_variants"), names.index("render_web_preview"))


if __name__ == "__main__":
    unittest.main()
