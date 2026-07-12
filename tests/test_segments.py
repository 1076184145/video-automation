from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from video_automation.segments import _probe_duration, _segment_ranges


class SegmentPlanningTests(unittest.TestCase):
    def test_probe_rejects_non_finite_duration(self) -> None:
        result = SimpleNamespace(returncode=0, stdout="inf\n", stderr="")
        settings = SimpleNamespace(ffprobe_path=Path("ffprobe"))

        with patch("video_automation.segments.subprocess.run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "invalid duration"):
                _probe_duration(settings, Path("video.mp4"))

    def test_segment_ranges_reject_non_finite_values(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "finite"):
            _segment_ranges(float("nan"), 60.0, [])
        with self.assertRaisesRegex(RuntimeError, "finite"):
            _segment_ranges(120.0, float("inf"), [])

    def test_segment_ranges_reject_excessive_fanout(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "too many segments"):
            _segment_ranges(100_000.0, 60.0, [])

    def test_segment_ranges_preserve_normal_planning(self) -> None:
        self.assertEqual(
            _segment_ranges(125.0, 60.0, []),
            [(0.0, 60.0), (60.0, 120.0), (120.0, 125.0)],
        )


if __name__ == "__main__":
    unittest.main()
