from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from video_automation.api import create_server


class QueueHttpApiTests(unittest.TestCase):
    def test_legacy_process_route_persists_then_executes_through_smart_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp4"
            source.write_bytes(b"video")
            settings = SimpleNamespace(
                root=root,
                jobs_dir=root / "processing" / "jobs",
                api_host="127.0.0.1",
                api_port=0,
                api_parallel_jobs=1,
                api_allowed_origins=(),
                api_batch_limit=10,
            )

            def finish_job(_settings, job, **_options):
                job.set_status("done")

            with patch("video_automation.api.process_job", side_effect=finish_job):
                server = create_server(settings)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                host, port = server.server_address
                connection = http.client.HTTPConnection(host, port, timeout=5)
                try:
                    body = json.dumps({"path": str(source), "priority": 7}).encode("utf-8")
                    connection.request(
                        "POST",
                        "/process",
                        body=body,
                        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
                    )
                    response = connection.getresponse()
                    submitted = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(response.status, 202)
                    self.assertTrue(submitted["queue"]["id"].startswith("queue_"))
                    self.assertEqual(submitted["queue"]["priority"], 7)

                    deadline = time.time() + 3
                    queue_payload = {}
                    while time.time() < deadline:
                        connection.request("GET", "/api/v1/queue")
                        response = connection.getresponse()
                        queue_payload = json.loads(response.read().decode("utf-8"))
                        if queue_payload["items"][0]["status"] == "completed":
                            break
                        time.sleep(0.02)

                    self.assertEqual(queue_payload["items"][0]["status"], "completed")
                    self.assertEqual(queue_payload["items"][0]["job_name"], submitted["id"])
                finally:
                    connection.close()
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
