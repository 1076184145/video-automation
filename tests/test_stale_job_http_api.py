from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from video_automation.api import create_server


class StaleJobHttpApiTests(unittest.TestCase):
    def test_stale_running_job_can_be_stopped_then_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            jobs_dir = root / "processing" / "jobs"
            job_dir = jobs_dir / "stale-job"
            job_dir.mkdir(parents=True)
            (job_dir / "job.json").write_text(
                json.dumps({
                    "source_path": str(root / "source.mp4"),
                    "job_dir": str(job_dir),
                    "status": "transcribing",
                    "created_at": "2026-07-14T09:30:10",
                    "updated_at": "2026-07-14T10:20:56",
                    "current_stage": "transcribe",
                }),
                encoding="utf-8",
            )
            web_dir = root / "web"
            web_dir.mkdir()
            (web_dir / "index.html").write_text("ok", encoding="utf-8")
            settings = SimpleNamespace(
                root=root,
                jobs_dir=jobs_dir,
                api_host="127.0.0.1",
                api_port=0,
                api_parallel_jobs=1,
                api_allowed_origins=(),
            )
            server = create_server(settings, start_queue_worker=False)  # type: ignore[arg-type]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            try:
                connection.request(
                    "POST",
                    "/jobs/stale-job/cancel",
                    body=b"{}",
                    headers={"Content-Type": "application/json", "Content-Length": "2"},
                )
                response = connection.getresponse()
                stopped = json.loads(response.read().decode("utf-8"))

                self.assertEqual(response.status, 200)
                self.assertEqual(stopped["status"], "canceled")
                self.assertIn("no active worker", stopped["stage_message"])
                self.assertTrue(stopped["runtime"]["can_delete"])

                connection.request("DELETE", "/jobs/stale-job")
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 200)
                self.assertFalse(job_dir.exists())
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
