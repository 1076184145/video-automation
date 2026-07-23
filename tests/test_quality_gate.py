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
            (job_dir / "subtitles_clipped.ass").write_text(
                "[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,第一行\\N第二行\\N第三行\n",
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
            (job_dir / "subtitles_clipped.ass").write_text(
                "[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                "Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,第一行\\N第二行\n",
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

    def test_long_transcript_segment_passes_when_renderer_splits_it_into_valid_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            (job_dir / "final.mp4").write_bytes(b"video")
            (job_dir / "manifest.json").write_text(
                '{"duration_seconds":30,"width":1920,"height":1080}', encoding="utf-8"
            )
            (job_dir / "transcript.json").write_text(
                json.dumps({"segments": [{"start": 0, "end": 8, "text": "这是一段很长的转写文本，字幕生成器会把它拆成多个时间片，因此不应该按原始段落总字数误判。"}]}),
                encoding="utf-8",
            )
            (job_dir / "subtitles_clipped.ass").write_text(
                "[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
                "Dialogue: 0,0:00:00.00,0:00:04.00,Default,,0,0,0,,第一段第一行\\N第一段第二行\n"
                "Dialogue: 0,0:00:04.00,0:00:08.00,Default,,0,0,0,,第二段第一行\\N第二段第二行\n",
                encoding="utf-8",
            )

            result = evaluate_quality_gate(job_dir, {
                "subtitle_max_chars_per_line": 8,
                "subtitle_max_lines": 2,
            })

            self.assertNotIn("subtitle_overflow", {item["code"] for item in result["blocking"]})
            self.assertIn("subtitles_fit", {item["code"] for item in result["passed"]})

    def test_missing_rendered_subtitles_is_advisory_instead_of_guessing_from_transcript_length(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            (job_dir / "final.mp4").write_bytes(b"video")
            (job_dir / "manifest.json").write_text(
                '{"duration_seconds":30,"width":1920,"height":1080}', encoding="utf-8"
            )
            (job_dir / "transcript.json").write_text(
                json.dumps({"segments": [{"start": 0, "end": 8, "text": "这是一段很长的转写文本"}]}),
                encoding="utf-8",
            )

            result = evaluate_quality_gate(job_dir, {"subtitle_max_lines": 2})

            self.assertNotIn("subtitle_overflow", {item["code"] for item in result["blocking"]})
            self.assertIn("subtitles_unverified", {item["code"] for item in result["advisory"]})

    def test_missing_render_output_is_always_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            (job_dir / "manifest.json").write_text(
                '{"duration_seconds":10,"width":1920,"height":1080}', encoding="utf-8"
            )
            result = evaluate_quality_gate(job_dir, {})
            self.assertIn("render_missing", {item["code"] for item in result["blocking"]})

    def test_unresolved_clip_refinement_is_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp)
            (job_dir / "final.mp4").write_bytes(b"video")
            (job_dir / "manifest.json").write_text(
                '{"duration_seconds":10,"width":1920,"height":1080}',
                encoding="utf-8",
            )
            (job_dir / "clip_refinement.json").write_text(
                json.dumps(
                    {
                        "status": "needs_review",
                        "stop_reason": "manual_review_required",
                        "final_report": {"score": "invalid-local-state"},
                    }
                ),
                encoding="utf-8",
            )

            result = evaluate_quality_gate(job_dir, {})

            self.assertIn(
                "clip_refinement_required",
                {item["code"] for item in result["blocking"]},
            )


if __name__ == "__main__":
    unittest.main()
