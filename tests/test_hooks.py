from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from video_automation.config import Settings
from video_automation.hooks import generate_uvr_plan


class AudioSeparationHookTests(unittest.TestCase):
    def test_plan_mode_writes_non_executing_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            settings = replace(Settings.load(), audio_separation_engine="plan", uvr_path=None)

            payload = generate_uvr_plan(settings, job_dir, force=True)

            self.assertEqual(payload["status"], "not_configured")
            self.assertEqual(payload["engine"], "plan")
            self.assertIn("recommended_outputs", payload)
            self.assertTrue((job_dir / "uvr_plan.json").exists())

    def test_demucs_mode_missing_tool_is_reported_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            (job_dir / "audio_hq.flac").write_bytes(b"fake audio")
            settings = replace(
                Settings.load(),
                audio_separation_engine="demucs",
                demucs_path=Path("definitely-missing-demucs"),
                audio_separation_timeout_seconds=60,
            )

            payload = generate_uvr_plan(settings, job_dir, force=True)

            self.assertEqual(payload["status"], "missing_tool")
            self.assertEqual(payload["engine"], "demucs")
            self.assertIn("DEMUCS_PATH", payload["error"])


if __name__ == "__main__":
    unittest.main()
