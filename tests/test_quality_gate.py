from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from video_automation.quality_gate import evaluate_quality_gate


class QualityGateTests(unittest.TestCase):
    def test_gate_reports_aspect_subtitle_cover_and_volume_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            (job_dir / "final.mp4").write_bytes(b"video")
            (job_dir / "manifest.json").write_text(
                json.dumps({
                    "duration_seconds": 120,
                    "width": 1920,
                    "height": 1080,
                    "audio_loudness_lufs": -30,
                }),
                encoding="utf-8",
            )
            (job_dir / "transcript.json").write_text(
                json.dumps({"segments": [{"start": 0, "end": 1, "text": "这是一条明显超过单行限制的字幕文本"}]}),
                encoding="utf-8",
            )

            result = evaluate_quality_gate(job_dir, {
                "aspect": "9:16",
                "subtitle_max_chars_per_line": 8,
                "subtitle_max_lines": 2,
                "cover_required": True,
                "loudness_min_lufs": -20,
                "loudness_max_lufs": -10,
            })

            codes = {item["code"] for item in result["blocking"]}
            self.assertEqual(result["status"], "blocked")
            self.assertTrue({"aspect_ratio", "subtitle_overflow", "cover_missing", "audio_loudness"}.issubset(codes))

    def test_compliant_job_passes_and_missing_optional_loudness_is_advisory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            (job_dir / "review.mp4").write_bytes(b"video")
            (job_dir / "cover_vertical.jpg").write_bytes(b"cover")
            (job_dir / "manifest.json").write_text(
                json.dumps({"duration_seconds": 30, "width": 1080, "height": 1920}),
                encoding="utf-8",
            )
            (job_dir / "transcript.json").write_text(
                json.dumps({"segments": [{"start": 0, "end": 1, "text": "短字幕"}]}),
                encoding="utf-8",
            )

            result = evaluate_quality_gate(job_dir, {
                "aspect": "9:16",
                "subtitle_max_chars_per_line": 12,
                "subtitle_max_lines": 2,
                "cover_required": True,
                "loudness_min_lufs": -20,
                "loudness_max_lufs": -10,
            })

            self.assertEqual(result["status"], "advisory")
            self.assertFalse(result["blocking"])
            self.assertIn("audio_loudness_missing", {item["code"] for item in result["advisory"]})

    def test_missing_render_output_is_always_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            (job_dir / "manifest.json").write_text(
                '{"duration_seconds":10,"width":1920,"height":1080}', encoding="utf-8"
            )
            result = evaluate_quality_gate(job_dir, {})
            self.assertIn("render_missing", {item["code"] for item in result["blocking"]})


if __name__ == "__main__":
    unittest.main()
