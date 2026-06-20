from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from video_automation.jobs import create_job, load_job


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


if __name__ == "__main__":
    unittest.main()
