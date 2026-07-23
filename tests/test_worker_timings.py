from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from video_automation.task_queue import QueueControlRequested
from video_automation.pipeline_executor import _transcription_backend_label
from video_automation.pipeline_scheduler import PipelineStage, ProgressReporter, run_pipeline


class StageTimingTests(unittest.TestCase):
    def test_transcription_backend_label_prefers_funasr_for_fallback_mode(self) -> None:
        self.assertEqual(_transcription_backend_label("funasr-whisper"), "FunASR")
        self.assertEqual(_transcription_backend_label("faster-whisper"), "Faster-Whisper")
        self.assertEqual(_transcription_backend_label("cli"), "Whisper CLI")

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

    def test_run_pipeline_persists_resource_wait_and_execution_breakdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = SimpleNamespace(
                job_dir=root / "job",
                source_path=root / "source.mp4",
                status="pending",
                start_stage=lambda *_args, **_kwargs: None,
                complete_stage=lambda: None,
            )
            context = {}

            def run_stage(stage_context):
                stage_context.setdefault("_stage_metrics", {})["transcribe"] = {
                    "resource_wait_seconds": 1.25,
                    "execution_seconds": 2.5,
                }

            run_pipeline(
                ProgressReporter(False),
                job,
                [PipelineStage("transcribe", "transcribing", True, run_stage)],
                context,
            )

            payload = json.loads((job.job_dir / "stage_timings.json").read_text(encoding="utf-8"))
            timing = payload["stages"][0]
            self.assertEqual(timing["resource_wait_seconds"], 1.25)
            self.assertEqual(timing["execution_seconds"], 2.5)

    def test_pipeline_honors_queue_control_before_starting_next_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            called = []
            job = SimpleNamespace(job_dir=root / "job", source_path=root / "source.mp4", status="pending")
            stage = PipelineStage("probe", "probing", True, lambda _context: called.append(True))
            with self.assertRaises(QueueControlRequested) as raised:
                run_pipeline(
                    ProgressReporter(False), job, [stage], {},
                    control_callback=lambda: "paused",
                )
            self.assertEqual(raised.exception.action, "paused")
            self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
