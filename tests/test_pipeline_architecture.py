from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from video_automation import pipeline_executor, pipeline_scheduler, worker


class PipelineArchitectureTests(unittest.TestCase):
    def test_worker_is_a_thin_entrypoint_with_compatibility_exports(self) -> None:
        source = Path(worker.__file__).read_text(encoding="utf-8")

        self.assertLessEqual(len(source.splitlines()), 800)
        self.assertNotIn("def process_job(", source)
        self.assertNotIn("def run_pipeline(", source)
        self.assertNotIn("class PipelineStage", source)
        self.assertIs(worker.process_job, pipeline_executor.process_job)
        self.assertIs(worker.run_pipeline, pipeline_scheduler.run_pipeline)

    def test_pipeline_modules_do_not_depend_on_worker_entrypoint(self) -> None:
        executor_source = inspect.getsource(pipeline_executor)
        scheduler_source = inspect.getsource(pipeline_scheduler)

        self.assertNotIn("from .worker", executor_source)
        self.assertNotIn("from .worker", scheduler_source)
        self.assertEqual(pipeline_executor.process_job.__module__, "video_automation.pipeline_executor")
        self.assertEqual(pipeline_scheduler.run_pipeline.__module__, "video_automation.pipeline_scheduler")


if __name__ == "__main__":
    unittest.main()
