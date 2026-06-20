from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video_automation.cuts import generate_cuts
from video_automation.io_utils import write_json_atomic


class SemanticHighlightCutTests(unittest.TestCase):
    def test_highlights_feed_final_score_without_reordering_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_json_atomic(job_dir / "silence.json", {
                "status": "ok",
                "min_gap_seconds": 0,
                "silences": [{"start": 10.0, "end": 12.0}],
            })
            write_json_atomic(job_dir / "freeze.json", {"status": "skipped", "freezes": []})
            write_json_atomic(job_dir / "scene.json", {"status": "ok", "scenes": []})
            write_json_atomic(job_dir / "transcript.json", {
                "segments": [
                    {"start": 1.0, "end": 8.0, "text": "普通开场说明"},
                    {"start": 13.0, "end": 22.0, "text": "这里突然情绪激动并出现核心爆点"},
                ]
            })
            write_json_atomic(job_dir / "highlights.json", {
                "status": "ready",
                "summary": "第二段更适合切片",
                "highlights": [
                    {
                        "start": 13.0,
                        "end": 22.0,
                        "score": 92,
                        "reason": "情绪激动，适合短视频开头",
                        "recommended_use": "15 秒爆点",
                    }
                ],
            })

            cuts = generate_cuts(job_dir, 30.0, force=True)
            clips = cuts["clips"]

            self.assertLess(clips[0]["start"], clips[1]["start"])
            self.assertEqual(clips[1]["semantic_score"], 92.0)
            self.assertIn("情绪激动", clips[1]["semantic_reasons"][0])
            self.assertGreater(clips[1]["final_score"], clips[0]["final_score"])
            self.assertEqual(clips[1]["final_rank"], 1)
            self.assertEqual(cuts["content_scoring"]["method"], "0.4*structure_score+0.6*semantic_score")


if __name__ == "__main__":
    unittest.main()
