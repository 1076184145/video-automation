from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video_automation.highlight_cut import generate_highlight_cut
from video_automation.io_utils import read_json_file, write_json_atomic
from video_automation.config import Settings
from video_automation.render import generate_highlight_render_preview


class HighlightCutTests(unittest.TestCase):
    def test_selects_highest_scoring_clips_within_target_duration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_json_atomic(job_dir / "cuts.json", {
                "duration_seconds": 120,
                "clips": [
                    {"start": 0, "end": 20, "duration": 20, "keep": True, "final_score": 40, "content_score": 40},
                    {"start": 22, "end": 40, "duration": 18, "keep": True, "final_score": 95, "semantic_reasons": ["爆点"]},
                    {"start": 45, "end": 70, "duration": 25, "keep": True, "final_score": 82, "semantic_reasons": ["情绪"]},
                    {"start": 80, "end": 110, "duration": 30, "keep": False, "final_score": 100},
                ],
            })

            manifest = generate_highlight_cut(job_dir, target_seconds=45, force=True)

            self.assertEqual(manifest["status"], "ready")
            self.assertEqual(manifest["target_seconds"], 45)
            self.assertEqual(manifest["selected_clip_count"], 2)
            self.assertEqual(manifest["duration_seconds"], 43)
            self.assertEqual([clip["start"] for clip in manifest["clips"]], [22.0, 45.0])
            self.assertEqual(manifest["clips"][0]["selection_rank"], 1)
            self.assertEqual(manifest["clips"][1]["selection_rank"], 2)
            self.assertTrue((job_dir / "highlight_cut.json").exists())
            self.assertEqual(read_json_file(job_dir / "highlight_cut.json")["duration_seconds"], 43)

    def test_highlight_render_preview_uses_highlight_cut_clips(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            source_path = job_dir / "source.mp4"
            source_path.write_bytes(b"placeholder")
            write_json_atomic(job_dir / "highlight_cut.json", {
                "status": "ready",
                "duration_seconds": 12,
                "clips": [
                    {"start": 10, "end": 16, "duration": 6},
                    {"start": 30, "end": 36, "duration": 6},
                ],
            })

            preview = generate_highlight_render_preview(Settings.load(), job_dir, source_path, force=True)

            self.assertEqual(preview["status"], "ready")
            self.assertEqual(preview["output_path"], str(job_dir / "highlight.mp4"))
            self.assertEqual(preview["clip_count"], 2)
            self.assertEqual([clip["start"] for clip in preview["clips"]], [10, 30])
            self.assertTrue((job_dir / "highlight_render_preview.json").exists())

    def test_prefers_precise_semantic_intervals_over_whole_structural_clips(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_json_atomic(job_dir / "cuts.json", {
                "duration_seconds": 240,
                "clips": [
                    {
                        "start": 0,
                        "end": 183,
                        "duration": 183,
                        "keep": True,
                        "final_score": 98,
                        "content_score": 45,
                    },
                ],
            })
            write_json_atomic(job_dir / "highlights.json", {
                "status": "ready",
                "summary": "挑战成功",
                "highlights": [
                    {
                        "start": 72,
                        "end": 88,
                        "score": 96,
                        "reason": "完成高难度挑战后大笑",
                        "recommended_use": "开场爆点",
                    },
                ],
            })
            write_json_atomic(job_dir / "transcript.json", {
                "segments": [
                    {"start": 70, "end": 80, "text": "再试一次"},
                    {"start": 80, "end": 90, "text": "成功了，太好笑了"},
                ],
            })

            manifest = generate_highlight_cut(job_dir, target_seconds=60, force=True)

            self.assertEqual(manifest["selection"]["candidate_source"], "semantic_highlights")
            self.assertEqual(manifest["duration_seconds"], 16)
            self.assertEqual(manifest["clips"][0]["start"], 72)
            self.assertEqual(manifest["clips"][0]["end"], 88)
            self.assertIn("成功了", manifest["clips"][0]["transcript_text"])

    def test_trims_an_oversized_structural_fallback_to_the_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_json_atomic(job_dir / "cuts.json", {
                "duration_seconds": 240,
                "clips": [
                    {
                        "start": 0,
                        "end": 183,
                        "duration": 183,
                        "keep": True,
                        "final_score": 98,
                        "content_score": 98,
                    },
                ],
            })

            manifest = generate_highlight_cut(job_dir, target_seconds=60, force=True)

            self.assertEqual(manifest["selection"]["candidate_source"], "structural_clips")
            self.assertEqual(manifest["duration_seconds"], 60)
            self.assertTrue(manifest["clips"][0]["trimmed_to_target"])
            self.assertEqual(manifest["clips"][0]["start"], 61.5)
            self.assertEqual(manifest["clips"][0]["end"], 121.5)

    def test_ignores_sub_three_second_noise_before_oversized_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_json_atomic(job_dir / "cuts.json", {
                "duration_seconds": 240,
                "clips": [
                    {
                        "start": 188.036,
                        "end": 188.632,
                        "duration": 0.596,
                        "keep": True,
                        "final_score": 23.4,
                    },
                    {
                        "start": 0,
                        "end": 183.427,
                        "duration": 183.427,
                        "keep": True,
                        "final_score": 10.6,
                    },
                ],
            })

            manifest = generate_highlight_cut(job_dir, target_seconds=60, force=True)

            self.assertEqual(manifest["duration_seconds"], 60)
            self.assertTrue(manifest["clips"][0]["trimmed_to_target"])
            self.assertGreater(manifest["clips"][0]["start"], 0)


if __name__ == "__main__":
    unittest.main()
