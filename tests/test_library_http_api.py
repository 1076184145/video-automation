from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from video_automation.api import create_server


class LibraryHttpApiTests(unittest.TestCase):
    def test_server_rejects_second_live_instance_on_same_port(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = SimpleNamespace(
                root=root,
                jobs_dir=root / "processing" / "jobs",
                api_host="127.0.0.1",
                api_port=0,
                api_parallel_jobs=1,
                api_allowed_origins=(),
            )
            web_dir = root / "web"
            web_dir.mkdir()
            (web_dir / "index.html").write_text("ok", encoding="utf-8")
            first = create_server(settings, start_queue_worker=False)  # type: ignore[arg-type]
            try:
                occupied = SimpleNamespace(**vars(settings))
                occupied.api_port = first.server_address[1]
                with self.assertRaises(OSError):
                    create_server(occupied, start_queue_worker=False)  # type: ignore[arg-type]
            finally:
                first.server_close()

    def test_versioned_library_routes_are_served_by_the_existing_http_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = SimpleNamespace(
                root=root,
                jobs_dir=root / "processing" / "jobs",
                api_host="127.0.0.1",
                api_port=0,
                api_parallel_jobs=1,
                api_allowed_origins=(),
            )
            web_root = root / "web"
            web_root.mkdir()
            (web_root / "index.html").write_text("<!doctype html><title>App</title>", encoding="utf-8")
            server = create_server(settings, start_queue_worker=False)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            connection = http.client.HTTPConnection(host, port, timeout=5)
            try:
                connection.request("GET", "/")
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    response.getheader("Content-Security-Policy"),
                    "frame-ancestors 'none'",
                )
                self.assertEqual(response.getheader("X-Frame-Options"), "DENY")

                connection.request("GET", "/api/v1/capabilities")
                response = connection.getresponse()
                capabilities = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 200)
                self.assertTrue(capabilities["features"]["projects"])

                body = json.dumps({"name": "HTTP Project"}).encode("utf-8")
                connection.request(
                    "POST",
                    "/api/v1/projects",
                    body=body,
                    headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
                )
                response = connection.getresponse()
                project = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 201)
                self.assertEqual(project["name"], "HTTP Project")
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_saving_review_edits_creates_a_revision_available_from_v1_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs_dir = root / "processing" / "jobs"
            job_dir = jobs_dir / "job-review"
            job_dir.mkdir(parents=True)
            (job_dir / "job.json").write_text(
                json.dumps({
                    "source_path": str(root / "source.mp4"),
                    "job_dir": str(job_dir),
                    "status": "needs_review",
                    "created_at": "2026-07-04T10:00:00",
                    "updated_at": "2026-07-04T10:00:00",
                }),
                encoding="utf-8",
            )
            settings = SimpleNamespace(
                root=root,
                jobs_dir=jobs_dir,
                api_host="127.0.0.1",
                api_port=0,
                api_parallel_jobs=1,
                api_allowed_origins=(),
            )
            cuts = {"clips": [{"start": 0, "end": 2, "label": "保留"}]}
            with (
                patch("video_automation.api.update_cuts_from_editor", return_value=cuts),
                patch("video_automation.api.generate_clipped_ass_subtitles"),
                patch("video_automation.api.generate_render_preview"),
                patch("video_automation.api._remove_render_outputs"),
            ):
                server = create_server(settings, start_queue_worker=False)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                host, port = server.server_address
                connection = http.client.HTTPConnection(host, port, timeout=5)
                try:
                    body = json.dumps({"clips": cuts["clips"]}).encode("utf-8")
                    connection.request(
                        "POST",
                        "/jobs/job-review/cuts",
                        body=body,
                        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
                    )
                    response = connection.getresponse()
                    saved = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(response.status, 200)
                    self.assertEqual(saved["revision"]["revision"], 1)

                    connection.request("GET", "/api/v1/jobs/job-review/revisions")
                    response = connection.getresponse()
                    revisions = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(response.status, 200)
                    self.assertEqual(revisions["items"][0]["kind"], "cuts")
                finally:
                    connection.close()
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
