from __future__ import annotations

import unittest
from types import SimpleNamespace

from video_automation.profiles import apply_profile_settings
from video_automation.render import _x264_encoding_args
from video_automation.config import Settings


def _setting(**overrides):
    values = {
        "export_platforms": ("douyin",),
        "render_x264_preset": "medium",
        "render_x264_crf": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class X264EncodingTests(unittest.TestCase):
    def test_x264_final_keeps_platform_crf_when_override_is_zero(self) -> None:
        args = _x264_encoding_args(_setting(render_x264_crf=0), final=True)  # type: ignore[arg-type]

        self.assertEqual(args[args.index("-preset") + 1], "medium")
        self.assertEqual(args[args.index("-crf") + 1], "21")

    def test_x264_final_can_use_speed_preset_and_crf_override(self) -> None:
        args = _x264_encoding_args(
            _setting(render_x264_preset="veryfast", render_x264_crf=23),
            final=True,  # type: ignore[arg-type]
        )

        self.assertEqual(args[args.index("-preset") + 1], "veryfast")
        self.assertEqual(args[args.index("-crf") + 1], "23")

    def test_fast_profile_switches_cpu_x264_to_faster_preset(self) -> None:
        settings = apply_profile_settings(Settings.load(), "fast")

        self.assertEqual(settings.render_x264_preset, "veryfast")
        self.assertEqual(settings.render_x264_crf, 23)


if __name__ == "__main__":
    unittest.main()
