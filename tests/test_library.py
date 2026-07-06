from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from video_automation.library import LibraryRepository


class LibraryRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.jobs_dir = self.root / "jobs"
        self.repository = LibraryRepository(self.root / "library.sqlite3")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_projects_and_creator_kits_round_trip_structured_values(self) -> None:
        kit = self.repository.create_creator_kit(
            {
                "name": "竖屏知识号",
                "platform": "douyin",
                "aspect": "9:16",
                "subtitle_style": {"font": "Microsoft YaHei", "size": 44},
                "cover_style": {"tone": "clean"},
                "metadata_style": {"voice": "concise"},
                "hotwords": ["Video Automation", "FFmpeg"],
                "replacements": {"F F mpeg": "FFmpeg"},
                "outro": {"enabled": False},
            }
        )
        project = self.repository.create_project(
            {
                "name": "每周直播精选",
                "description": "直播切片系列",
                "tags": ["直播", "周更"],
                "default_kit_id": kit["id"],
            }
        )

        self.assertEqual(self.repository.get_project(project["id"])["tags"], ["直播", "周更"])
        self.assertEqual(self.repository.get_creator_kit(kit["id"])["subtitle_style"]["size"], 44)
        self.assertEqual(len(self.repository.list_projects()), 1)
        self.assertEqual(len(self.repository.list_creator_kits()), 1)

    def test_job_index_is_idempotent_and_preserves_project_assignment(self) -> None:
        project = self.repository.create_project({"name": "Series A"})
        job_dir = self.jobs_dir / "20260704-demo"
        job_dir.mkdir(parents=True)
        state_path = job_dir / "job.json"
        state = {
            "source_path": str(self.root / "demo.mp4"),
            "job_dir": str(job_dir),
            "status": "pending",
            "created_at": "2026-07-04T10:00:00",
            "updated_at": "2026-07-04T10:00:00",
        }
        state_path.write_text(json.dumps(state), encoding="utf-8")

        self.assertEqual(self.repository.index_existing_jobs(self.jobs_dir), 1)
        self.repository.assign_job("20260704-demo", project_id=project["id"])
        state["status"] = "done"
        state["updated_at"] = "2026-07-04T11:00:00"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.repository.index_existing_jobs(self.jobs_dir), 0)

        jobs = self.repository.list_indexed_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["project_id"], project["id"])
        self.assertEqual(jobs[0]["status"], "done")

    def test_creator_kit_snapshots_remain_immutable_after_kit_update(self) -> None:
        kit = self.repository.create_creator_kit(
            {"name": "Default", "platform": "bilibili", "subtitle_style": {"size": 48}}
        )
        snapshot = self.repository.snapshot_creator_kit(kit["id"])
        self.repository.update_creator_kit(
            kit["id"],
            {"name": "Default", "platform": "bilibili", "subtitle_style": {"size": 56}},
        )

        stored = self.repository.get_creator_kit_snapshot(snapshot["id"])
        self.assertEqual(stored["payload"]["subtitle_style"]["size"], 48)
        self.assertEqual(self.repository.get_creator_kit(kit["id"])["subtitle_style"]["size"], 56)

    def test_revisions_are_numbered_per_job_and_keep_immutable_payloads(self) -> None:
        first = self.repository.create_revision(
            "job-one",
            "transcript",
            {"segments": [{"start": 0, "end": 1, "text": "第一版"}]},
            summary="保存字幕",
        )
        second = self.repository.create_revision(
            "job-one",
            "cuts",
            {"clips": [{"start": 0, "end": 1}]},
            summary="保存片段",
        )

        self.assertEqual(first["revision"], 1)
        self.assertEqual(second["revision"], 2)
        self.assertEqual(self.repository.latest_revision_number("job-one"), 2)
        self.assertEqual(
            [item["kind"] for item in self.repository.list_revisions("job-one")],
            ["cuts", "transcript"],
        )
        stored = self.repository.get_revision(first["id"])
        self.assertEqual(stored["payload"]["segments"][0]["text"], "第一版")

    def test_job_contexts_are_loaded_in_bulk_with_latest_revision(self) -> None:
        for name in ("job-a", "job-b"):
            self.repository.index_job(name, job_dir=self.jobs_dir / name)
        self.repository.create_revision("job-a", "cuts", {"clips": []})

        contexts = self.repository.get_job_contexts(["job-a", "job-b"])

        self.assertEqual(contexts["job-a"]["revision"], 1)
        self.assertEqual(contexts["job-b"]["revision"], 0)


if __name__ == "__main__":
    unittest.main()
