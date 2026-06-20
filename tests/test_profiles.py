from __future__ import annotations

import unittest

from video_automation.profiles import profile_flags


class ProfilePresetTests(unittest.TestCase):
    def test_one_click_profiles_render_final_without_redundant_review(self) -> None:
        for profile in ("douyin", "bilibili", "youtube_shorts"):
            with self.subTest(profile=profile):
                flags = profile_flags(profile)
                self.assertTrue(flags["render_final"])
                self.assertFalse(flags["render_review"])


if __name__ == "__main__":
    unittest.main()
