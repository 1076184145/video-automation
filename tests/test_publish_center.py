from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video_automation.publish_center import PublishRepository, PublishService


class PublishRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = PublishRepository(Path(self.temp_dir.name) / "library.sqlite3")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_attempt_state_machine_tracks_progress_without_storing_credentials(self) -> None:
        attempt = self.repository.create_attempt(
            "job-one",
            "bilibili",
            credential_ref="bilibili:creator-one",
            payload={"title": "Demo"},
            total_bytes=100,
            manual_package_path="D:/jobs/job-one/publish_package.json",
        )
        validating = self.repository.transition(attempt["id"], "validating")
        uploading = self.repository.transition(validating["id"], "uploading")
        progress = self.repository.record_progress(uploading["id"], 64, upload_url="sandbox://upload/1")
        processing = self.repository.transition(progress["id"], "processing", remote_id="BV1demo")
        published = self.repository.transition(processing["id"], "published")

        self.assertEqual(published["status"], "published")
        self.assertEqual(published["uploaded_bytes"], 64)
        self.assertEqual(published["remote_id"], "BV1demo")
        self.assertEqual(published["credential_ref"], "bilibili:creator-one")
        self.assertNotIn("access_token", str(published))

    def test_invalid_publish_transition_is_rejected(self) -> None:
        attempt = self.repository.create_attempt("job-one", "bilibili", payload={})
        with self.assertRaisesRegex(ValueError, "invalid publish transition"):
            self.repository.transition(attempt["id"], "published")


class PublishServiceTests(unittest.TestCase):
    def test_missing_permission_falls_back_to_manual_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "publish_package.json"
            package.write_text("{}", encoding="utf-8")
            repository = PublishRepository(root / "library.sqlite3")

            class UnavailableProvider:
                def validate(self, _attempt):
                    raise PermissionError("Bilibili publish permission is unavailable")

            service = PublishService(repository, {"bilibili": UnavailableProvider()})
            attempt = repository.create_attempt(
                "job-one",
                "bilibili",
                payload={},
                manual_package_path=str(package),
            )
            failed = service.run_attempt(attempt["id"])

            self.assertEqual(failed["status"], "failed")
            self.assertFalse(failed["retryable"])
            self.assertEqual(failed["action"], "open_manual_package")
            self.assertEqual(failed["manual_package_path"], str(package))

    def test_provider_upload_updates_processing_then_syncs_published(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = PublishRepository(Path(tmp) / "library.sqlite3")

            class Provider:
                def validate(self, _attempt):
                    return {"ok": True}

                def upload(self, _attempt, progress):
                    progress(5, "sandbox://upload")
                    progress(10, "sandbox://upload")
                    return {"remote_id": "BV1demo", "status": "processing"}

                def query(self, _attempt):
                    return {"remote_id": "BV1demo", "status": "published"}

            service = PublishService(repository, {"bilibili": Provider()})
            attempt = repository.create_attempt(
                "job-one", "bilibili", payload={}, total_bytes=10
            )
            processing = service.run_attempt(attempt["id"])
            published = service.sync_attempt(processing["id"])

            self.assertEqual(processing["status"], "processing")
            self.assertEqual(processing["uploaded_bytes"], 10)
            self.assertEqual(published["status"], "published")


if __name__ == "__main__":
    unittest.main()
