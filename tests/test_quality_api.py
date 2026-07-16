from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

from video_automation.api import create_server
from video_automation.api import _record_transcript_preferences
from video_automation.library_api import (
    dispatch_library_request,
    preference_repository_for,
    repository_for,
)


class QualityApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = SimpleNamespace(
            root=root,
            jobs_dir=root / "processing" / "jobs",
            api_host="127.0.0.1",
            api_port=0,
            api_parallel_jobs=1,
            api_allowed_origins=(),
            api_batch_limit=10,
        )
        self.job_dir = self.settings.jobs_dir / "job-one"
        self.job_dir.mkdir(parents=True)
        (self.job_dir / "job.json").write_text(json.dumps({
            "source_path": str(root / "source.mp4"),
            "job_dir": str(self.job_dir),
            "status": "needs_review",
            "created_at": "2026-07-05T10:00:00",
            "updated_at": "2026-07-05T10:00:00",
        }), encoding="utf-8")
        (self.job_dir / "final.mp4").write_bytes(b"video")
        (self.job_dir / "manifest.json").write_text(
            '{"duration_seconds":30,"width":1920,"height":1080}', encoding="utf-8"
        )
        (self.job_dir / "transcript.json").write_text(
            '{"segments":[{"start":0,"end":1,"text":"short"}]}', encoding="utf-8"
        )
        repository = repository_for(self.settings)
        kit = repository.create_creator_kit({
            "name": "竖屏严格套件",
            "aspect": "9:16",
            "subtitle_style": {"max_chars_per_line": 12, "max_lines": 2},
            "cover_style": {"required": False},
        })
        snapshot = repository.snapshot_creator_kit(kit["id"])
        repository.index_existing_jobs(self.settings.jobs_dir)
        repository.assign_job("job-one", creator_kit_snapshot_id=snapshot["id"])

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_quality_route_uses_creator_kit_snapshot_and_blocks_approval(self) -> None:
        status, quality = dispatch_library_request(
            self.settings, "GET", "/api/v1/jobs/job-one/quality"
        )
        self.assertEqual(status, 200)
        self.assertEqual(quality["status"], "blocked")
        self.assertIn("aspect_ratio", {item["code"] for item in quality["blocking"]})

        server = create_server(self.settings, start_queue_worker=False)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        connection = http.client.HTTPConnection(host, port, timeout=5)
        try:
            connection.request("POST", "/jobs/job-one/approve", body=b"{}", headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.status, 409)
            self.assertEqual(payload["error"]["code"], "quality_gate_failed")
            self.assertEqual(payload["error"]["details"]["status"], "blocked")
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_preferences_routes_export_and_clear_local_learning(self) -> None:
        preference_repository_for(self.settings).record(
            "publish_selection", {"platform": "bilibili"}, job_name="job-one"
        )

        status, summary = dispatch_library_request(self.settings, "GET", "/api/v1/preferences")
        self.assertEqual(status, 200)
        self.assertEqual(summary["platforms"]["bilibili"], 1)

        status, exported = dispatch_library_request(
            self.settings, "GET", "/api/v1/preferences/export"
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(exported["events"]), 1)

        status, cleared = dispatch_library_request(
            self.settings, "DELETE", "/api/v1/preferences"
        )
        self.assertEqual(status, 200)
        self.assertEqual(cleared["cleared"], 1)

    def test_transcript_corrections_are_recorded_as_local_preferences(self) -> None:
        repository = preference_repository_for(self.settings)
        _record_transcript_preferences(
            repository,
            "job-one",
            {"segments": [{"start": 0, "end": 1, "text": "旧词"}]},
            {"segments": [{"start": 0, "end": 1, "text": "新词"}]},
        )
        self.assertEqual(repository.summary()["subtitle_replacements"], {"旧词": "新词"})

    def test_quality_http_route_decodes_non_ascii_job_names(self) -> None:
        unicode_name = "任务-中文"
        unicode_dir = self.settings.jobs_dir / unicode_name
        unicode_dir.mkdir()
        for filename in ("job.json", "manifest.json", "transcript.json", "final.mp4"):
            source = self.job_dir / filename
            target = unicode_dir / filename
            target.write_bytes(source.read_bytes())
        server = create_server(self.settings, start_queue_worker=False)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        connection = http.client.HTTPConnection(host, port, timeout=5)
        try:
            connection.request("GET", f"/api/v1/jobs/{quote(unicode_name)}/quality")
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 200)
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
