from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from video_automation.library import LibraryRepository
from video_automation.task_queue import QueueRepository


class Phase3StressTests(unittest.TestCase):
    def test_1000_task_index_lookup_and_100_item_queue_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "library.sqlite3"
            library = LibraryRepository(database)
            for index in range(1000):
                library.index_job(
                    f"job-{index}",
                    job_dir=root / "jobs" / f"job-{index}",
                    source_path=root / "recordings" / f"video-{index}.mp4",
                    status="done",
                )

            started = time.perf_counter()
            jobs = library.list_indexed_jobs()
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.assertEqual(len(jobs), 1000)
            self.assertLess(elapsed_ms, 100)

            queue = QueueRepository(database)
            for index in range(100):
                queue.enqueue(f"queued-{index}", {"path": f"video-{index}.mp4"})
            for _ in range(4):
                queue.claim_next(worker_pid=999)
            self.assertEqual(queue.recover_interrupted("9999-01-01T00:00:00"), 4)
            self.assertEqual(len(queue.list_items()), 100)


if __name__ == "__main__":
    unittest.main()
