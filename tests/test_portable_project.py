from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from video_automation.automation import AutomationRepository
from video_automation.library import LibraryRepository
from video_automation.portable_project import create_portable_project_package


class PortableProjectTests(unittest.TestCase):
    def test_package_contains_project_context_recipe_and_review_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = SimpleNamespace(jobs_dir=root / "processing" / "jobs")
            database = root / "processing" / "library.sqlite3"
            library = LibraryRepository(database)
            recipes = AutomationRepository(database)
            recipe = recipes.create_recipe({"name": "Shorts", "stages": ["transcribe"], "options": {}})
            project = library.create_project({"name": "Series"})
            job_dir = settings.jobs_dir / "任务一"
            job_dir.mkdir(parents=True)
            (job_dir / "job.json").write_text('{"status":"done"}', encoding="utf-8")
            (job_dir / "transcript.srt").write_text("subtitle", encoding="utf-8")
            (job_dir / "cover_selected.jpg").write_bytes(b"cover")
            library.index_job("任务一", job_dir=job_dir, status="done")
            library.assign_job("任务一", project_id=project["id"], recipe_id=recipe["id"])

            result = create_portable_project_package(settings, library, recipes, project["id"])

            with zipfile.ZipFile(result["path"]) as archive:
                names = set(archive.namelist())
                self.assertIn("project.json", names)
                self.assertIn("jobs/任务一/job.json", names)
                self.assertIn("jobs/任务一/transcript.srt", names)
                self.assertIn("jobs/任务一/cover_selected.jpg", names)
                payload = json.loads(archive.read("project.json"))
                self.assertEqual(payload["project"]["name"], "Series")
                self.assertEqual(payload["recipes"][0]["name"], "Shorts")
                self.assertNotIn("final.mp4", names)

    def test_package_sanitizes_job_names_used_as_archive_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = SimpleNamespace(jobs_dir=root / "processing" / "jobs")
            database = root / "processing" / "library.sqlite3"
            library = LibraryRepository(database)
            recipes = AutomationRepository(database)
            project = library.create_project({"name": "Series"})
            job_dir = settings.jobs_dir / "safe-folder"
            job_dir.mkdir(parents=True)
            (job_dir / "job.json").write_text('{"status":"done"}', encoding="utf-8")
            library.index_job("../escape", job_dir=job_dir, status="done")
            library.assign_job("../escape", project_id=project["id"])

            result = create_portable_project_package(settings, library, recipes, project["id"])

            with zipfile.ZipFile(result["path"]) as archive:
                names = set(archive.namelist())
                self.assertIn("jobs/escape/job.json", names)
                self.assertFalse(any(".." in Path(name).parts for name in names))


if __name__ == "__main__":
    unittest.main()
