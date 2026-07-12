from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from video_automation.jobs import MAX_PERSISTED_ERROR_BYTES, create_job, load_job


class JobBatchMetadataTests(unittest.TestCase):
    def test_batch_metadata_round_trips_through_job_state(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = SimpleNamespace(jobs_dir=root / "jobs")
            source = root / "input.mp4"

            job = create_job(
                settings,
                source,
                batch_id="batch-20260612-demo",
                batch_index=2,
                batch_size=4,
            )
            loaded = load_job(job.state_path)

            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.batch_id, "batch-20260612-demo")
            self.assertEqual(loaded.batch_index, 2)
            self.assertEqual(loaded.batch_size, 4)
            self.assertEqual(
                loaded.to_dict()["batch_id"],
                "batch-20260612-demo",
            )

    def test_same_stem_sources_created_at_same_time_have_distinct_job_ids(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = SimpleNamespace(jobs_dir=root / "jobs")
            first_source = root / "first" / "clip.mp4"
            second_source = root / "second" / "clip.mov"

            with patch("video_automation.jobs.utc_stamp", return_value="20260101-120000"):
                first = create_job(settings, first_source)
                second = create_job(settings, second_source)

            self.assertNotEqual(first.job_dir, second.job_dir)
            self.assertEqual(load_job(first.state_path).source_path, first_source.resolve())
            self.assertEqual(load_job(second.state_path).source_path, second_source.resolve())

    def test_same_source_still_reuses_existing_job_without_force(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = SimpleNamespace(jobs_dir=root / "jobs")
            source = root / "clip.mp4"

            first = create_job(settings, source)
            second = create_job(settings, source)

            self.assertEqual(first.job_dir, second.job_dir)

    def test_failed_job_bounds_persisted_error_before_advice_duplication(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = SimpleNamespace(jobs_dir=root / "jobs")
            job = create_job(settings, root / "clip.mp4")

            job.fail("错误" * MAX_PERSISTED_ERROR_BYTES)
            loaded = load_job(job.state_path)

            assert loaded is not None and loaded.error is not None
            self.assertLessEqual(len(loaded.error.encode("utf-8")), MAX_PERSISTED_ERROR_BYTES)
            self.assertTrue(loaded.error.endswith("...[error truncated]"))


if __name__ == "__main__":
    unittest.main()
