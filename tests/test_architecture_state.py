from __future__ import annotations

import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from video_automation.api import _execute_queue_item, create_server
from video_automation.config import Settings
from video_automation.jobs import InvalidJobTransition, Job
from video_automation.library import LibraryRepository
from video_automation.library_api import (
    delete_job_records,
    library_database_path,
    preference_repository_for,
    publish_repository_for,
    queue_repository_for,
)
from video_automation.runtime_config import apply_runtime_settings_snapshot, snapshot_runtime_settings
from video_automation.stage_runs import StageRunRepository
from video_automation.task_queue import QueueService


def _settings(root: Path) -> Settings:
    return replace(
        Settings.load(),
        root=root,
        jobs_dir=root / "processing" / "jobs",
        logs_dir=root / "logs",
        api_host="127.0.0.1",
        api_port=0,
        api_parallel_jobs=1,
    )


class ArchitectureStateTests(unittest.TestCase):
    def test_runtime_snapshot_freezes_non_secret_values_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original = replace(
                _settings(Path(tmp)),
                whisper_model="model-at-submit",
                render_video_encoder="h264_nvenc",
                google_api_key="must-not-be-persisted",
            )
            snapshot = snapshot_runtime_settings(original)
            current = replace(
                original,
                whisper_model="model-after-submit",
                render_video_encoder="libx264",
                google_api_key="new-live-secret",
                jobs_dir=Path(tmp) / "other-jobs",
            )

            restored = apply_runtime_settings_snapshot(current, snapshot)

            self.assertEqual(restored.whisper_model, "model-at-submit")
            self.assertEqual(restored.render_video_encoder, "h264_nvenc")
            self.assertEqual(restored.google_api_key, "new-live-secret")
            self.assertEqual(restored.jobs_dir, Path(tmp) / "other-jobs")
            self.assertNotIn("google_api_key", snapshot["values"])

    def test_invalid_terminal_transition_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = Job(root / "source.mp4", root / "job", status="done")
            with self.assertRaises(InvalidJobTransition):
                job.set_status("paused")

    def test_queued_execution_uses_submit_time_settings_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            submitted_settings = replace(_settings(root), whisper_model="submit-model")
            job = Job(root / "source.mp4", submitted_settings.jobs_dir / "job-snapshot", status="queued")
            job.save()
            queue = queue_repository_for(submitted_settings)
            item = queue.enqueue(
                "job-snapshot",
                {
                    "path": str(job.source_path),
                    "_runtime_settings_snapshot": snapshot_runtime_settings(submitted_settings),
                },
            )
            observed: list[str] = []

            def process(run_settings, run_job, **_options):
                observed.append(run_settings.whisper_model)
                run_job.set_status("done")

            current_settings = replace(submitted_settings, whisper_model="changed-after-submit")
            with patch("video_automation.api.process_job", side_effect=process):
                _execute_queue_item(current_settings, item)

            self.assertEqual(observed, ["submit-model"])

    def test_rerun_submission_is_durable_and_does_not_start_api_thread(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _settings(Path(tmp))
            job = Job(Path(tmp) / "source.mp4", settings.jobs_dir / "job-rerun", status="needs_review")
            job.save()
            server = create_server(settings, start_queue_worker=False)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            try:
                body = json.dumps({"stage": "transcribe"}).encode("utf-8")
                connection.request(
                    "POST",
                    "/jobs/job-rerun/rerun",
                    body=body,
                    headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
                )
                response = connection.getresponse()
                payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 202)
                self.assertEqual(payload["status"], "queued")
                self.assertEqual(payload["queue"]["status"], "pending")
                self.assertEqual(payload["queue"]["retry_stage"], "transcribe")
                self.assertIn("_runtime_settings_snapshot", payload["queue"]["payload"])
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_long_enhancement_is_executed_by_managed_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _settings(Path(tmp))
            job = Job(Path(tmp) / "source.mp4", settings.jobs_dir / "job-enhance", status="needs_review")
            job.save()
            server = create_server(settings, start_queue_worker=False)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            try:
                body = json.dumps({"platform": "douyin", "force": True}).encode("utf-8")
                connection.request(
                    "POST",
                    "/jobs/job-enhance/metadata/generate",
                    body=body,
                    headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
                )
                response = connection.getresponse()
                submitted = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 202)
                self.assertEqual(submitted["queue"]["payload"]["_command"], "generate_metadata")

                with patch("video_automation.api.generate_metadata", return_value={"title": "ready"}) as generate:
                    service = QueueService(
                        queue_repository_for(settings),
                        lambda item: _execute_queue_item(settings, item),
                    )
                    self.assertTrue(service.run_once())

                generate.assert_called_once()
                completed = queue_repository_for(settings).get(submitted["queue"]["id"])
                self.assertEqual(completed["status"], "completed")
                self.assertEqual(json.loads(job.state_path.read_text(encoding="utf-8"))["status"], "needs_review")
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_delete_job_records_removes_all_database_projections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _settings(Path(tmp))
            database_path = library_database_path(settings)
            library = LibraryRepository(database_path)
            library.index_job("job-delete", job_dir=settings.jobs_dir / "job-delete")
            queue_repository_for(settings).enqueue("job-delete", {"path": "source.mp4"})
            stages = StageRunRepository(database_path)
            run_id = stages.start_pipeline("job-delete", total_stages=1)
            stages.record_stage(run_id, "job-delete", "probe", stage_number=1, total_stages=1, status="complete")
            stages.finish_pipeline(run_id, "complete")
            preference_repository_for(settings).record("clip_feedback", {}, job_name="job-delete")
            publish_repository_for(settings).create_attempt("job-delete", "bilibili", payload={})

            delete_job_records(settings, "job-delete")

            connection = sqlite3.connect(database_path)
            try:
                for table in (
                    "job_index",
                    "task_queue",
                    "pipeline_runs",
                    "stage_runs",
                    "preference_events",
                    "publish_attempts",
                ):
                    count = connection.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE job_name = ?", ("job-delete",)
                    ).fetchone()[0]
                    self.assertEqual(count, 0, table)
            finally:
                connection.close()

    def test_delete_waits_for_worker_cancellation_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = _settings(Path(tmp))
            job = Job(Path(tmp) / "source.mp4", settings.jobs_dir / "job-active", status="queued")
            job.save()
            queue = queue_repository_for(settings)
            item = queue.enqueue("job-active", {"path": str(job.source_path)})
            queue.claim_next(worker_pid=12345)
            server = create_server(settings, start_queue_worker=False)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            try:
                connection.request("DELETE", "/jobs/job-active")
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 409)

                requested = queue.cancel(item["id"])
                self.assertEqual(requested["status"], "running")
                connection.request("DELETE", "/jobs/job-active")
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 409)

                queue.acknowledge_control(item["id"], "canceled")
                job.cancel()
                connection.request("DELETE", "/jobs/job-active")
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 200)
                self.assertFalse(job.job_dir.exists())
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
