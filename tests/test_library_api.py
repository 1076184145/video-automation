from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from video_automation.library_api import (
    attach_job_context,
    dispatch_library_request,
    job_library_fields,
    repository_for,
)


class LibraryApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = SimpleNamespace(
            root=root,
            jobs_dir=root / "processing" / "jobs",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_capabilities_and_project_crud_use_versioned_contract(self) -> None:
        status, capabilities = dispatch_library_request(
            self.settings, "GET", "/api/v1/capabilities"
        )
        self.assertEqual(status, 200)
        self.assertTrue(capabilities["features"]["projects"])
        self.assertTrue(capabilities["features"]["creator_kits"])
        self.assertTrue(capabilities["features"]["revisions"])

        status, created = dispatch_library_request(
            self.settings,
            "POST",
            "/api/v1/projects",
            {"name": "周更项目", "tags": ["周更"]},
        )
        self.assertEqual(status, 201)
        self.assertTrue(created["id"].startswith("project_"))

        status, listed = dispatch_library_request(self.settings, "GET", "/api/v1/projects")
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in listed["items"]], [created["id"]])

        status, updated = dispatch_library_request(
            self.settings,
            "POST",
            f"/api/v1/projects/{created['id']}",
            {"name": "周更精选"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["name"], "周更精选")

        status, deleted = dispatch_library_request(
            self.settings, "DELETE", f"/api/v1/projects/{created['id']}"
        )
        self.assertEqual(status, 200)
        self.assertTrue(deleted["deleted"])

    def test_creator_kit_creation_and_validation_return_structured_errors(self) -> None:
        status, error = dispatch_library_request(
            self.settings, "POST", "/api/v1/creator-kits", {"name": ""}
        )
        self.assertEqual(status, 400)
        self.assertEqual(error["error"]["code"], "validation_error")
        self.assertFalse(error["error"]["retryable"])

        status, created = dispatch_library_request(
            self.settings,
            "POST",
            "/api/v1/creator-kits",
            {"name": "B站横屏", "platform": "bilibili", "aspect": "16:9"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(created["platform"], "bilibili")

        status, missing = dispatch_library_request(
            self.settings, "GET", "/api/v1/creator-kits/missing"
        )
        self.assertEqual(status, 404)
        self.assertEqual(missing["error"]["code"], "not_found")

    def test_repository_indexes_existing_jobs_before_serving_library_requests(self) -> None:
        self.settings.jobs_dir.mkdir(parents=True)
        job_dir = self.settings.jobs_dir / "existing-job"
        job_dir.mkdir()
        (job_dir / "job.json").write_text(
            '{"source_path":"D:/video.mp4","status":"done","created_at":"2026-07-04T10:00:00","updated_at":"2026-07-04T11:00:00"}',
            encoding="utf-8",
        )

        repository = repository_for(self.settings)
        self.assertEqual([item["job_name"] for item in repository.list_indexed_jobs()], ["existing-job"])

    def test_job_context_snapshots_the_selected_kit_and_exposes_stable_fields(self) -> None:
        repository = repository_for(self.settings)
        kit = repository.create_creator_kit({"name": "竖屏", "aspect": "9:16"})
        project = repository.create_project({"name": "Project", "default_kit_id": kit["id"]})
        job_dir = self.settings.jobs_dir / "job-one"
        job_dir.mkdir(parents=True)
        job = SimpleNamespace(
            job_dir=job_dir,
            source_path=Path("D:/source.mp4"),
            status="pending",
            created_at="2026-07-04T10:00:00",
            updated_at="2026-07-04T10:00:00",
        )

        attached = attach_job_context(
            self.settings,
            job,
            {"project_id": project["id"], "creator_kit_id": kit["id"]},
        )
        fields = job_library_fields(self.settings, "job-one")

        self.assertEqual(fields["id"], "job-one")
        self.assertEqual(fields["project_id"], project["id"])
        self.assertEqual(fields["creator_kit_snapshot_id"], attached["creator_kit_snapshot_id"])
        self.assertIn("review", fields["capabilities"])

        attach_job_context(self.settings, job, {})
        preserved = job_library_fields(self.settings, "job-one")
        self.assertEqual(preserved["project_id"], project["id"])
        self.assertEqual(preserved["creator_kit_snapshot_id"], attached["creator_kit_snapshot_id"])

    def test_revision_routes_list_and_return_saved_review_versions(self) -> None:
        repository = repository_for(self.settings)
        revision = repository.create_revision(
            "job-one",
            "transcript",
            {"segments": [{"start": 0, "end": 1, "text": "可恢复"}]},
        )

        status, listed = dispatch_library_request(
            self.settings, "GET", "/api/v1/jobs/job-one/revisions"
        )
        self.assertEqual(status, 200)
        self.assertEqual(listed["items"][0]["revision"], 1)
        self.assertNotIn("payload", listed["items"][0])

        status, restored = dispatch_library_request(
            self.settings,
            "GET",
            f"/api/v1/jobs/job-one/revisions/{revision['id']}",
        )
        self.assertEqual(status, 200)
        self.assertEqual(restored["payload"]["segments"][0]["text"], "可恢复")
        self.assertEqual(job_library_fields(self.settings, "job-one")["revision"], 1)


if __name__ == "__main__":
    unittest.main()
