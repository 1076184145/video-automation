from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from video_automation.cleanup import cleanup_jobs


class CleanupTests(unittest.TestCase):
    def _job(self, jobs_dir: Path, name: str, status: str, *, age_days: int = 10) -> Path:
        job_dir = jobs_dir / name
        job_dir.mkdir(parents=True)
        (job_dir / "job.json").write_text(json.dumps({"status": status}), encoding="utf-8")
        old = time.time() - age_days * 86400
        os.utime(job_dir, (old, old))
        return job_dir

    def test_cleanup_rejects_non_positive_retention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = SimpleNamespace(jobs_dir=Path(tmp))
            for days in (0, -1):
                with self.subTest(days=days):
                    with self.assertRaisesRegex(ValueError, "positive"):
                        cleanup_jobs(settings, days=days)

    def test_full_cleanup_skips_running_and_unknown_job_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)
            done = self._job(jobs_dir, "done-job", "done")
            running = self._job(jobs_dir, "running-job", "transcribing")
            unknown = jobs_dir / "unknown-job"
            unknown.mkdir()
            old = time.time() - 10 * 86400
            os.utime(unknown, (old, old))

            result = cleanup_jobs(SimpleNamespace(jobs_dir=jobs_dir), days=7)

            self.assertFalse(done.exists())
            self.assertTrue(running.exists())
            self.assertTrue(unknown.exists())
            self.assertEqual([item["job_dir"] for item in result["removed"]], [str(done)])
            self.assertEqual({item["reason"] for item in result["skipped"]}, {"active_job", "unknown_status"})

    def test_intermediate_cleanup_requires_completed_output_and_preserves_review_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)
            done = self._job(jobs_dir, "done-job", "done")
            (done / "final.mp4").write_bytes(b"final")
            (done / "review.mp4").write_bytes(b"review")
            (done / "transcript.json").write_text("{}", encoding="utf-8")
            (done / "audio.wav").write_bytes(b"audio-cache")
            (done / "audio_hq.flac").write_bytes(b"hq-cache")
            (done / "render.tmp.mp4").write_bytes(b"partial")
            old = time.time() - 10 * 86400
            os.utime(done, (old, old))

            result = cleanup_jobs(
                SimpleNamespace(jobs_dir=jobs_dir),
                days=7,
                mode="intermediates",
            )

            self.assertTrue(done.exists())
            self.assertTrue((done / "final.mp4").exists())
            self.assertTrue((done / "review.mp4").exists())
            self.assertTrue((done / "transcript.json").exists())
            self.assertFalse((done / "audio.wav").exists())
            self.assertFalse((done / "audio_hq.flac").exists())
            self.assertFalse((done / "render.tmp.mp4").exists())
            self.assertGreater(result["reclaimed_bytes"], 0)

    def test_intermediate_cleanup_dry_run_does_not_remove_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp)
            done = self._job(jobs_dir, "done-job", "done")
            (done / "final.mp4").write_bytes(b"final")
            cache = done / "audio.wav"
            cache.write_bytes(b"cache")
            old = time.time() - 10 * 86400
            os.utime(done, (old, old))

            result = cleanup_jobs(
                SimpleNamespace(jobs_dir=jobs_dir),
                days=7,
                mode="intermediates",
                dry_run=True,
            )

            self.assertTrue(cache.exists())
            self.assertEqual(result["candidates"], [str(cache)])


if __name__ == "__main__":
    unittest.main()
