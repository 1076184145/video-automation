from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video_automation.task_queue import QueueControlRequested, QueueRepository, QueueService


class QueueRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = QueueRepository(Path(self.temp_dir.name) / "library.sqlite3")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_claim_uses_priority_then_manual_position_and_global_pause(self) -> None:
        low = self.repository.enqueue("job-low", {"path": "low.mp4"}, priority=0)
        high = self.repository.enqueue("job-high", {"path": "high.mp4"}, priority=10)
        medium = self.repository.enqueue("job-medium", {"path": "medium.mp4"}, priority=0)
        self.repository.reorder([medium["id"], low["id"], high["id"]])

        self.repository.set_global_paused(True)
        self.assertIsNone(self.repository.claim_next(worker_pid=123))
        self.repository.set_global_paused(False)

        self.assertEqual(self.repository.claim_next(worker_pid=123)["job_name"], "job-high")
        self.repository.complete(high["id"])
        self.assertEqual(self.repository.claim_next(worker_pid=123)["job_name"], "job-medium")

    def test_item_pause_resume_cancel_and_single_stage_retry_are_persistent(self) -> None:
        item = self.repository.enqueue("job-one", {"path": "one.mp4"})
        paused = self.repository.pause(item["id"])
        self.assertEqual(paused["status"], "paused")
        resumed = self.repository.resume(item["id"])
        self.assertEqual(resumed["status"], "pending")

        running = self.repository.claim_next(worker_pid=77)
        self.repository.fail(running["id"], "GPU memory exhausted")
        retried = self.repository.retry_stage(running["id"], "transcribe")
        self.assertEqual(retried["status"], "pending")
        self.assertEqual(retried["retry_stage"], "transcribe")
        self.assertEqual(retried["attempt"], 1)

        canceled = self.repository.cancel(running["id"])
        self.assertEqual(canceled["status"], "canceled")

    def test_interrupted_running_items_return_to_pending_after_restart(self) -> None:
        item = self.repository.enqueue("job-recover", {"path": "recover.mp4"})
        self.repository.claim_next(worker_pid=88)

        recovered = self.repository.recover_interrupted("9999-01-01T00:00:00")

        self.assertEqual(recovered, 1)
        restored = self.repository.get(item["id"])
        self.assertEqual(restored["status"], "pending")
        self.assertIsNone(restored["worker_pid"])


class QueueServiceTests(unittest.TestCase):
    def test_run_once_records_success_and_failure_without_losing_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = QueueRepository(Path(tmp) / "library.sqlite3")
            repository.enqueue("job-ok", {"value": 1}, priority=2)
            repository.enqueue("job-fail", {"value": 2}, priority=1)
            seen = []

            def execute(item):
                seen.append((item["job_name"], item["payload"]["value"]))
                if item["job_name"] == "job-fail":
                    raise RuntimeError("render failed")

            service = QueueService(repository, execute)
            self.assertTrue(service.run_once())
            self.assertTrue(service.run_once())

            items = {item["job_name"]: item for item in repository.list_items()}
            self.assertEqual(seen, [("job-ok", 1), ("job-fail", 2)])
            self.assertEqual(items["job-ok"]["status"], "completed")
            self.assertEqual(items["job-fail"]["status"], "failed")
            self.assertEqual(items["job-fail"]["error"], "render failed")

    def test_cooperative_pause_and_cancel_keep_items_out_of_failed_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = QueueRepository(Path(tmp) / "library.sqlite3")
            paused = repository.enqueue("job-pause", {})

            service = QueueService(repository, lambda _item: (_ for _ in ()).throw(QueueControlRequested("paused")))
            self.assertTrue(service.run_once())
            self.assertEqual(repository.get(paused["id"])["status"], "paused")

            canceled = repository.enqueue("job-cancel", {})
            service = QueueService(repository, lambda _item: (_ for _ in ()).throw(QueueControlRequested("canceled")))
            self.assertTrue(service.run_once())
            self.assertEqual(repository.get(canceled["id"])["status"], "canceled")


if __name__ == "__main__":
    unittest.main()
