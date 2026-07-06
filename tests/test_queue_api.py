from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from video_automation.library_api import dispatch_library_request, queue_repository_for


class QueueApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = SimpleNamespace(root=root, jobs_dir=root / "processing" / "jobs")
        self.repository = queue_repository_for(self.settings)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_queue_routes_control_global_and_item_state(self) -> None:
        first = self.repository.enqueue("job-one", {"path": "one.mp4"})
        second = self.repository.enqueue("job-two", {"path": "two.mp4"})

        status, listed = dispatch_library_request(self.settings, "GET", "/api/v1/queue")
        self.assertEqual(status, 200)
        self.assertEqual(listed["count"], 2)
        self.assertFalse(listed["paused"])

        status, paused = dispatch_library_request(
            self.settings, "POST", "/api/v1/queue/pause", {}
        )
        self.assertEqual(status, 200)
        self.assertTrue(paused["paused"])

        status, item = dispatch_library_request(
            self.settings, "POST", f"/api/v1/queue/{first['id']}/pause", {}
        )
        self.assertEqual(status, 200)
        self.assertEqual(item["status"], "paused")

        status, reordered = dispatch_library_request(
            self.settings,
            "POST",
            "/api/v1/queue/reorder",
            {"ids": [second["id"], first["id"]]},
        )
        self.assertEqual(status, 200)
        self.assertEqual([entry["id"] for entry in reordered["items"]], [second["id"], first["id"]])

    def test_retry_stage_route_returns_structured_not_found(self) -> None:
        status, missing = dispatch_library_request(
            self.settings,
            "POST",
            "/api/v1/queue/missing/retry-stage",
            {"stage": "transcribe"},
        )
        self.assertEqual(status, 404)
        self.assertEqual(missing["error"]["code"], "not_found")


if __name__ == "__main__":
    unittest.main()
