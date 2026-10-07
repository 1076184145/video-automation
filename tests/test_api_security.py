from __future__ import annotations

import unittest
import http.client
import json
import os
import tempfile
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from video_automation.api import create_server
from video_automation.api_security import (
    UnsafeAPIBindingError,
    allowed_request_host,
    api_binding_status,
    is_loopback_api_host,
)
from video_automation.config import Settings


class ApiSecurityTests(unittest.TestCase):
    def test_loopback_host_detection_accepts_only_local_bindings(self) -> None:
        for host in ("127.0.0.1", "127.12.34.56", "::1", "[::1]", "localhost", "LOCALHOST."):
            with self.subTest(host=host):
                self.assertTrue(is_loopback_api_host(host))
        for host in ("0.0.0.0", "::", "[::]", "192.168.1.5", "example.test", ""):
            with self.subTest(host=host):
                self.assertFalse(is_loopback_api_host(host))

    def test_create_server_rejects_remote_binding_without_explicit_opt_in(self) -> None:
        settings = SimpleNamespace(api_host="0.0.0.0", api_allow_remote=False)

        with self.assertRaisesRegex(UnsafeAPIBindingError, "API_ALLOW_REMOTE"):
            create_server(settings, start_queue_worker=False)  # type: ignore[arg-type]

    def test_remote_binding_status_reports_explicit_high_visibility_warning(self) -> None:
        status = api_binding_status("0.0.0.0", allow_remote=True)

        self.assertTrue(status["remote_binding"])
        self.assertTrue(status["allowed"])
        self.assertEqual(status["warning_code"], "remote_api_exposed")

    def test_loopback_binding_needs_no_opt_in_or_warning(self) -> None:
        status = api_binding_status("127.0.0.1", allow_remote=False)

        self.assertFalse(status["remote_binding"])
        self.assertTrue(status["allowed"])
        self.assertEqual(status["warning_code"], "")

    def test_job_routes_reject_encoded_path_traversal(self) -> None:
        # The router unquotes each matched segment (routing.py), so %2F becomes
        # a real separator after matching; job_name must never escape jobs_dir.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.mkdir()
            (outside / "job.json").write_text(
                json.dumps({"source_path": "secret.mp4", "status": "done"}),
                encoding="utf-8",
            )
            settings = replace(
                Settings.load(),
                root=root,
                jobs_dir=root / "jobs",
                logs_dir=root / "logs",
                api_host="127.0.0.1",
                api_port=0,
                api_allow_remote=False,
                api_allowed_origins=(),
            )
            settings.jobs_dir.mkdir()
            web_root = root / "web"
            web_root.mkdir()
            (web_root / "index.html").write_text("ok", encoding="utf-8")
            server = create_server(settings, start_queue_worker=False)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_port
                paths = [
                    "/jobs/..%2Foutside",
                    "/jobs/%2E%2E%2Foutside",
                    "/jobs/..%2F..%2Foutside",
                    "/jobs/..",
                    "/jobs/..%2F",
                ]
                if os.name == "nt":
                    # On Windows a decoded backslash is also a separator.
                    paths.append("/jobs/..%5Coutside")
                for path in paths:
                    with self.subTest(path=path):
                        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                        connection.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
                        response = connection.getresponse()
                        body = response.read()
                        self.assertEqual(response.status, 400)
                        self.assertIn(b"invalid job", body)
                        connection.close()
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request("GET", "/jobs", headers={"Host": f"127.0.0.1:{port}"})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_host_guard_blocks_rebinding_without_origin_and_keeps_local_access(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = replace(
                Settings.load(),
                root=root,
                jobs_dir=root / "jobs",
                logs_dir=root / "logs",
                api_host="127.0.0.1",
                api_port=0,
                api_allow_remote=False,
                api_allowed_origins=(),
            )
            web_root = root / "web"
            web_root.mkdir()
            (web_root / "index.html").write_text("ok", encoding="utf-8")
            server = create_server(settings, start_queue_worker=False)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_port
                for host, path, expected in (
                    (f"evil.example:{port}", "/api/v1/capabilities", 421),
                    (f"127.0.0.1.evil.example:{port}", "/api/v1/capabilities", 421),
                    (f"localhost:{port}", "/api/v1/capabilities", 200),
                    (f"127.0.0.1:{port}", "/", 200),
                ):
                    with self.subTest(host=host):
                        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                        connection.request("GET", path, headers={"Host": host})
                        response = connection.getresponse()
                        self.assertEqual(response.status, expected)
                        response.read()
                        connection.close()
                server.RequestHandlerClass.api_context.replace_settings(
                    replace(settings, api_allow_remote=True)
                )
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request("GET", "/api/v1/capabilities", headers={"Host": f"evil.example:{port}"})
                response = connection.getresponse()
                self.assertEqual(response.status, 421)
                response.read()
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_host_guard_rejects_ambiguous_hosts_and_accepts_configured_proxy(self) -> None:
        common = dict(
            bound_host="127.0.0.1",
            bound_port=8765,
            allow_remote=False,
            allowed_origins=("https://review.example",),
        )
        for headers in ([], ["localhost:8765", "evil.example:8765"],
                        ["evil.example:8765"], ["localhost:bad"],
                        ["user@localhost:8765"]):
            with self.subTest(headers=headers):
                self.assertFalse(allowed_request_host(headers, **common))
        self.assertTrue(allowed_request_host(["review.example"], **common))
        self.assertFalse(allowed_request_host(
            ["evil.example:8765"], **{**common, "allow_remote": True}
        ))


if __name__ == "__main__":
    unittest.main()
