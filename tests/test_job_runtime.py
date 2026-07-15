from __future__ import annotations

import unittest
from unittest.mock import patch

from video_automation.api import _job_runtime_state


class JobRuntimeStateTests(unittest.TestCase):
    def test_completed_queue_with_running_job_file_is_stale_and_recoverable(self) -> None:
        runtime = _job_runtime_state(
            "transcribing",
            {"id": "queue-one", "status": "completed", "worker_pid": None},
            [],
        )

        self.assertFalse(runtime["active"])
        self.assertTrue(runtime["stale"])
        self.assertTrue(runtime["can_cancel"])
        self.assertTrue(runtime["can_delete"])

    @patch("video_automation.api._pid_is_alive", return_value=True)
    def test_live_queue_worker_keeps_running_job_protected(self, _alive) -> None:
        runtime = _job_runtime_state(
            "transcribing",
            {"id": "queue-one", "status": "running", "worker_pid": 1234},
            [],
        )

        self.assertTrue(runtime["active"])
        self.assertFalse(runtime["stale"])
        self.assertTrue(runtime["can_cancel"])
        self.assertFalse(runtime["can_delete"])

    def test_terminal_job_remains_deletable_without_cancel(self) -> None:
        runtime = _job_runtime_state("failed", None, [])

        self.assertFalse(runtime["active"])
        self.assertFalse(runtime["stale"])
        self.assertFalse(runtime["can_cancel"])
        self.assertTrue(runtime["can_delete"])


if __name__ == "__main__":
    unittest.main()
