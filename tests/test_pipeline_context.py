from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from video_automation.pipeline_context import PipelineContext
from video_automation.pipeline_scheduler import PipelineStage, ProgressReporter, run_pipeline


class PipelineContextTests(unittest.TestCase):
    def test_typed_context_supplies_scheduler_options_and_stage_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            context = PipelineContext(
                audio_path=root / "audio.wav",
                high_quality_audio_path=None,
                max_parallel_stages=2,
            )
            job = SimpleNamespace(
                job_dir=root / "job",
                source_path=root / "source.mp4",
                status="pending",
                start_stage=lambda *_args, **_kwargs: None,
                complete_stage=lambda: None,
            )

            def run_stage(stage_context: PipelineContext) -> None:
                stage_context.manifest = {"duration_seconds": 4.0}
                stage_context.record_stage_metrics(
                    "probe",
                    resource_wait_seconds=0.25,
                    execution_seconds=0.75,
                )

            run_pipeline(
                ProgressReporter(False),
                job,
                [PipelineStage("probe", "probing", True, run_stage)],
                context,
            )

            self.assertEqual(context.manifest, {"duration_seconds": 4.0})
            timing = __import__("json").loads(
                (job.job_dir / "stage_timings.json").read_text(encoding="utf-8")
            )["stages"][0]
            self.assertEqual(timing["resource_wait_seconds"], 0.25)
            self.assertEqual(timing["execution_seconds"], 0.75)

    def test_review_requirement_is_idempotent_and_job_local(self) -> None:
        context = PipelineContext(
            audio_path=Path("audio.wav"),
            high_quality_audio_path=None,
        )

        context.require_review("manual_review_required")
        context.require_review("manual_review_required")

        self.assertTrue(context.requires_review)
        self.assertEqual(context.review_reasons, ["manual_review_required"])


if __name__ == "__main__":
    unittest.main()
