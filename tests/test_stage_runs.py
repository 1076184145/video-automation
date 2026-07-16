from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from video_automation.stage_runs import StageRunRepository
from video_automation.worker import PipelineStage, ProgressReporter, run_pipeline


class StageRunRepositoryTests(unittest.TestCase):
    def test_pipeline_execution_is_projected_to_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = SimpleNamespace(
                job_dir=root / "jobs" / "job-one",
                source_path=root / "source.mp4",
                status="pending",
                start_stage=lambda *_args, **_kwargs: None,
                complete_stage=lambda: None,
            )
            repository = StageRunRepository(root / "library.sqlite3")
            stages = [
                PipelineStage("probe", "probing", True, lambda _context: None),
                PipelineStage("detect_freeze", "detecting_freeze", False, lambda _context: None),
            ]

            run_pipeline(
                ProgressReporter(False),
                job,
                stages,
                {},
                stage_repository=repository,
            )
            runs = repository.list_for_job("job-one")

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "complete")
        self.assertEqual(
            [(stage["stage"], stage["status"]) for stage in runs[0]["stages"]],
            [("probe", "complete"), ("detect_freeze", "skipped")],
        )
        self.assertIsInstance(runs[0]["worker_pid"], int)


if __name__ == "__main__":
    unittest.main()
