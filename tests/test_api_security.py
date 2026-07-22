from __future__ import annotations

import unittest
from types import SimpleNamespace

from video_automation.api import create_server
from video_automation.api_security import (
    UnsafeAPIBindingError,
    api_binding_status,
    is_loopback_api_host,
)


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


if __name__ == "__main__":
    unittest.main()
