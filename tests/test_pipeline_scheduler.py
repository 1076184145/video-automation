from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from video_automation.pipeline_scheduler import PipelineStage, ProgressReporter, build_pipeline_batches, run_pipeline


def _stage(
    name: str,
    *,
    dependencies: set[str] | None = None,
    resources: set[str] | None = None,
    run=lambda _context: None,
) -> PipelineStage:
    return PipelineStage(
        name,
        name,
        True,
        run,
        dependencies=frozenset(dependencies or set()),
        exclusive_resources=frozenset(resources or set()),
    )


class PipelineSchedulerTests(unittest.TestCase):
    def test_batches_follow_dependencies_and_preserve_ready_order(self) -> None:
        stages = [
            _stage("probe"),
            _stage("audio", dependencies={"probe"}),
            _stage("visual", dependencies={"probe"}),
            _stage("render", dependencies={"audio", "visual"}),
        ]

        batches = build_pipeline_batches(stages, max_parallel_stages=3)

        self.assertEqual([[stage.name for stage in batch] for batch in batches], [["probe"], ["audio", "visual"], ["render"]])

    def test_exclusive_resources_are_not_scheduled_in_same_batch(self) -> None:
        stages = [
            _stage("transcribe", resources={"gpu"}),
            _stage("render", resources={"gpu"}),
            _stage("silence"),
        ]

        batches = build_pipeline_batches(stages, max_parallel_stages=3)

        self.assertEqual([[stage.name for stage in batch] for batch in batches], [["transcribe", "silence"], ["render"]])

    def test_dependency_cycle_is_rejected(self) -> None:
        stages = [
            _stage("first", dependencies={"second"}),
            _stage("second", dependencies={"first"}),
        ]

        with self.assertRaisesRegex(ValueError, "dependency cycle"):
            build_pipeline_batches(stages, max_parallel_stages=2)

    def test_run_pipeline_executes_independent_stage_batch_concurrently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            barrier = threading.Barrier(2, timeout=2)
            entered: list[str] = []
            started: list[str] = []
            entered_lock = threading.Lock()

            def run_stage(name: str):
                def run(_context) -> None:
                    with entered_lock:
                        entered.append(name)
                    barrier.wait()

                return run

            job = SimpleNamespace(
                job_dir=root / "job",
                source_path=root / "source.mp4",
                status="pending",
                start_stage=lambda _status, stage, **_kwargs: started.append(stage),
                complete_stage=lambda: None,
            )
            stages = [_stage("audio", run=run_stage("audio")), _stage("visual", run=run_stage("visual"))]

            run_pipeline(ProgressReporter(False), job, stages, {"_max_parallel_stages": 2})

            self.assertCountEqual(entered, ["audio", "visual"])
            self.assertEqual(started, ["audio"])


if __name__ == "__main__":
    unittest.main()
