from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video_automation.preferences import PreferenceRepository


class PreferenceRepositoryTests(unittest.TestCase):
    def test_local_feedback_summary_learns_clip_subtitle_and_platform_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = PreferenceRepository(Path(tmp) / "library.sqlite3")
            repository.record("clip_feedback", {"action": "accepted", "reason": "high energy"}, job_name="job-one")
            repository.record("clip_feedback", {"action": "rejected", "reason": "silence"}, job_name="job-one")
            repository.record("subtitle_correction", {"before": "F F mpeg", "after": "FFmpeg"}, job_name="job-one")
            repository.record("publish_selection", {"platform": "bilibili"}, job_name="job-one")
            repository.record("publish_selection", {"platform": "bilibili"}, job_name="job-two")

            summary = repository.summary()

            self.assertEqual(summary["clip_feedback"]["accepted"], 1)
            self.assertEqual(summary["clip_feedback"]["rejected"], 1)
            self.assertEqual(summary["subtitle_replacements"]["F F mpeg"], "FFmpeg")
            self.assertEqual(summary["platforms"]["bilibili"], 2)
            self.assertEqual(summary["event_count"], 5)

    def test_preferences_can_be_exported_and_cleared_locally(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = PreferenceRepository(Path(tmp) / "library.sqlite3")
            repository.record("publish_selection", {"platform": "bilibili"})
            exported = repository.export()
            self.assertEqual(len(exported["events"]), 1)

            self.assertEqual(repository.clear(), 1)
            self.assertEqual(repository.summary()["event_count"], 0)


if __name__ == "__main__":
    unittest.main()
