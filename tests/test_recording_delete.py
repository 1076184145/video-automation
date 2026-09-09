from __future__ import annotations

import json
import http.client
import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from video_automation.api_system import delete_recording, recording_files
from video_automation.routing import CORE_ROUTER


class RecordingDeleteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.settings = SimpleNamespace(input_recordings_dir=self.root)
        self.source = self.root / "test.mp4"
        self.source.write_bytes(b"temporary-test-only")
        self.jobs = patch("video_automation.api_system.list_jobs", return_value=[])
        self.jobs.start()
        self.addCleanup(self.jobs.stop)

    def test_move_is_recoverable_and_not_listed(self):
        result = delete_recording(self.settings, "test.mp4")
        recovered = Path(result["recovery_path"])
        self.assertFalse(self.source.exists())
        self.assertEqual(recovered.read_bytes(), b"temporary-test-only")
        self.assertEqual(recording_files(self.settings), [])
        self.assertEqual(json.loads((recovered.parent / "restore.json").read_text())["original_path"], str(self.source))
        recovered.rename(self.source)
        self.assertTrue(self.source.exists())

    def test_rejects_escape_directories_and_non_media(self):
        (self.root / "note.txt").write_text("test")
        for value in ["../test.mp4", str(self.source), "", ".", "note.txt", ".deleted_recordings/test.mp4"]:
            with self.subTest(value=value), self.assertRaises((ValueError, FileNotFoundError)):
                delete_recording(self.settings, value)
        self.assertTrue(self.source.exists())

    def test_referenced_source_is_preserved(self):
        with patch("video_automation.api_system.list_jobs", return_value=[SimpleNamespace(source_path=self.source)]):
            with self.assertRaises(RuntimeError):
                delete_recording(self.settings, "test.mp4")
        self.assertTrue(self.source.exists())

    def test_move_failure_preserves_source(self):
        with patch.object(Path, "rename", side_effect=PermissionError("busy")):
            with self.assertRaises(PermissionError):
                delete_recording(self.settings, "test.mp4")
        self.assertTrue(self.source.exists())

    def test_route_exists(self):
        self.assertEqual(CORE_ROUTER.resolve("POST", "/recordings/delete").endpoint, "delete_recording")

    def test_http_confirmation_action_moves_only_requested_temporary_file(self):
        from video_automation.api import create_server
        self.settings.root = self.root
        self.settings.jobs_dir = self.root / "jobs"
        self.settings.api_host = "127.0.0.1"
        self.settings.api_port = 0
        self.settings.api_parallel_jobs = 1
        self.settings.api_allowed_origins = ()
        server = create_server(self.settings, start_queue_worker=False)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = http.client.HTTPConnection(*server.server_address, timeout=5)
        try:
            client.request("POST", "/recordings/delete", json.dumps({"relative_path": "test.mp4"}), {"Content-Type": "application/json"})
            response = client.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertTrue(Path(payload["recovery_path"]).is_file())
        finally:
            client.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
