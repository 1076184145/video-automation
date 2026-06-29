from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from video_automation.worker import PipelineStage, ProgressReporter, run_pipeline


class StageTimingTests(unittest.TestCase):
    def test_run_pipeline_persists_stage_timings_for_complete_and_skipped_stages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = SimpleNamespace(
                job_dir=root / "job",
                source_path=root / "source.mp4",
                status="pending",
                start_stage=lambda *_args, **_kwargs: None,
                complete_stage=lambda: None,
            )
            stages = [
                PipelineStage("probe", "probing", True, lambda _context: None),
                PipelineStage("detect_freeze", "detecting_freeze", False, lambda _context: None),
            ]

            run_pipeline(ProgressReporter(False), job, stages, {})

            payload = json.loads((job.job_dir / "stage_timings.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "complete")
            self.assertEqual(payload["total_stages"], 2)
            self.assertEqual(
                [(item["stage"], item["status"]) for item in payload["stages"]],
                [("probe", "complete"), ("detect_freeze", "skipped")],
            )
            self.assertIsInstance(payload["stages"][0]["duration_seconds"], float)
            self.assertEqual(payload["stages"][1]["reason"], "disabled")
            self.assertIsInstance(payload["total_duration_seconds"], float)
            self.assertGreaterEqual(payload["total_duration_seconds"], payload["stages"][0]["duration_seconds"])


if __name__ == "__main__":
    unittest.main()
