from __future__ import annotations

import unittest

from video_automation.config import Settings
from video_automation.profiles import apply_profile_settings, profile_flags


class ProfilePresetTests(unittest.TestCase):
    def test_one_click_profiles_render_final_without_redundant_review(self) -> None:
        for profile in ("fast", "douyin", "bilibili", "youtube_shorts"):
            with self.subTest(profile=profile):
                flags = profile_flags(profile)
                self.assertTrue(flags["render_final"])
                self.assertFalse(flags["render_review"])

    def test_fast_profile_skips_expensive_optional_analysis(self) -> None:
        flags = profile_flags("fast")

        self.assertTrue(flags["detect_silence"])
        self.assertTrue(flags["detect_scenes"])
        self.assertTrue(flags["burn_subtitles"])
        self.assertFalse(flags["detect_freeze"])
        self.assertFalse(flags["plan_crop"])

        settings = apply_profile_settings(Settings.load(), "fast")
        self.assertFalse(settings.source_integrity_scan_enabled)
        self.assertFalse(settings.high_quality_audio_enabled)
        self.assertFalse(settings.web_preview_enabled)


if __name__ == "__main__":
    unittest.main()
