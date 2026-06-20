from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from video_automation import covers, render
from video_automation.jobs import Job, load_job
from video_automation.subtitle_translation import _validate_translation_workload


class SecurityHardeningTests(unittest.TestCase):
    def test_load_job_ignores_serialized_job_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job_dir = root / "jobs" / "demo"
            job_dir.mkdir(parents=True)
            outside = root / "outside"
            state_path = job_dir / "job.json"
            state_path.write_text(
                json.dumps({
                    "source_path": str(root / "source.mp4"),
                    "job_dir": str(outside),
                    "status": "needs_review",
                }),
                encoding="utf-8",
            )

            job = load_job(state_path)

            self.assertIsNotNone(job)
            assert job is not None
            self.assertEqual(job.job_dir, job_dir)

    def test_crop_filter_allows_generated_templates_only(self) -> None:
        self.assertEqual(
            render._safe_crop_filter("crop=100:200:0:0,scale=1080:1920"),
            "crop=100:200:0:0,scale=1080:1920",
        )
        self.assertIsNone(render._safe_crop_filter("movie=/etc/passwd,scale=1080:1920"))
        self.assertIsNone(render._safe_crop_filter("crop=100:200:0:0;movie=http://127.0.0.1/x"))

    def test_remote_cover_image_rejects_private_hosts(self) -> None:
        for url in [
            "http://127.0.0.1/image.png",
            "http://localhost/image.png",
            "http://10.0.0.1/image.png",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/image.png",
        ]:
            with self.subTest(url=url):
                with self.assertRaises(RuntimeError):
                    covers._validate_remote_image_url(url)

    def test_translation_workload_has_global_limits(self) -> None:
        with self.assertRaises(RuntimeError):
            _validate_translation_workload([{"text": "x"} for _ in range(1201)])
        with self.assertRaises(RuntimeError):
            _validate_translation_workload([{"text": "x" * 240001}])


if __name__ == "__main__":
    unittest.main()
