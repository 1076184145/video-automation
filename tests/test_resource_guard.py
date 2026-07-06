from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from video_automation.api import _execute_queue_item
from video_automation.jobs import create_job
from video_automation.recovery import InsufficientDiskSpace, ensure_disk_capacity


class ResourceGuardTests(unittest.TestCase):
    def test_disk_guard_reports_required_and_available_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(InsufficientDiskSpace) as raised:
                ensure_disk_capacity(Path(tmp), required_bytes=200, free_bytes=lambda _: 100)
            self.assertEqual(raised.exception.required_bytes, 200)
            self.assertEqual(raised.exception.available_bytes, 100)

    def test_queue_does_not_start_pipeline_when_disk_preflight_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp4"
            source.write_bytes(b"video")
            settings = SimpleNamespace(jobs_dir=root / "jobs", min_free_disk_bytes=1024)
            job = create_job(settings, source)
            item = {"job_name": job.job_dir.name, "payload": {}, "retry_stage": None}

            with patch("video_automation.api.ensure_job_capacity", side_effect=InsufficientDiskSpace(1024, 12)), \
                 patch("video_automation.api.process_job") as process:
                with self.assertRaisesRegex(InsufficientDiskSpace, "disk space"):
                    _execute_queue_item(settings, item)
                process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
