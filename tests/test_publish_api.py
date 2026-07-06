from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from video_automation.library_api import dispatch_library_request, publish_service_for
from video_automation.credentials import MemoryCredentialStore
from video_automation.providers.bilibili import BilibiliHttpTransport


class PublishApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = SimpleNamespace(root=root, jobs_dir=root / "processing" / "jobs")
        self.job_dir = self.settings.jobs_dir / "job-one"
        self.job_dir.mkdir(parents=True)
        (self.job_dir / "final.mp4").write_bytes(b"video-bytes")
        (self.job_dir / "publish_package.json").write_text("{}", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_publish_attempt_routes_keep_manual_package_as_permanent_fallback(self) -> None:
        status, capabilities = dispatch_library_request(
            self.settings, "GET", "/api/v1/capabilities"
        )
        self.assertEqual(status, 200)
        self.assertTrue(capabilities["features"]["publish_connectors"])

        status, created = dispatch_library_request(
            self.settings,
            "POST",
            "/api/v1/publish-attempts",
            {
                "job_id": "job-one",
                "provider": "bilibili",
                "title": "演示视频",
                "credential_ref": "",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(created["status"], "draft")
        self.assertEqual(created["total_bytes"], len(b"video-bytes"))
        self.assertTrue(created["manual_package_path"].endswith("publish_package.json"))

        status, failed = dispatch_library_request(
            self.settings,
            "POST",
            f"/api/v1/publish-attempts/{created['id']}/start",
            {},
        )
        self.assertEqual(status, 200)
        self.assertEqual(failed["status"], "failed")
        self.assertFalse(failed["retryable"])
        self.assertEqual(failed["action"], "open_manual_package")

        status, listed = dispatch_library_request(
            self.settings, "GET", "/api/v1/publish-attempts"
        )
        self.assertEqual(status, 200)
        self.assertEqual(listed["items"][0]["id"], created["id"])

    def test_publish_targets_describe_authorization_and_fallback_without_secrets(self) -> None:
        status, targets = dispatch_library_request(
            self.settings, "GET", "/api/v1/publish-targets"
        )

        self.assertEqual(status, 200)
        self.assertEqual(targets["items"][0]["id"], "bilibili")
        self.assertTrue(targets["items"][0]["manual_fallback"])
        self.assertNotIn("access_token", str(targets))

    def test_approved_app_can_enable_configured_bilibili_or_sandbox_transport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = SimpleNamespace(
                root=root,
                jobs_dir=root / "processing" / "jobs",
                bilibili_api_base_url="https://sandbox.test",
                bilibili_api_endpoints={
                    "validate": "/validate",
                    "create_upload": "/upload/init",
                    "complete_upload": "/upload/complete",
                    "publish": "/publish",
                    "query": "/query/{remote_id}",
                },
            )

            service = publish_service_for(settings)

            self.assertIsInstance(service.providers["bilibili"].transport, BilibiliHttpTransport)

    def test_publish_credentials_are_written_only_to_secure_store_and_never_echoed(self) -> None:
        self.settings.credential_store = MemoryCredentialStore()
        status, saved = dispatch_library_request(
            self.settings,
            "POST",
            "/api/v1/publish-targets/bilibili/credentials",
            {
                "account_id": "creator-one",
                "client_id": "client-one",
                "access_token": "top-secret-token",
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(saved["credential_ref"], "bilibili:creator-one")
        self.assertNotIn("top-secret-token", str(saved))
        stored = self.settings.credential_store.get(saved["credential_ref"])
        self.assertIn("top-secret-token", stored)

        status, deleted = dispatch_library_request(
            self.settings,
            "DELETE",
            "/api/v1/publish-targets/bilibili/credentials/creator-one",
        )
        self.assertEqual(status, 200)
        self.assertTrue(deleted["deleted"])
        self.assertIsNone(self.settings.credential_store.get(saved["credential_ref"]))


if __name__ == "__main__":
    unittest.main()
