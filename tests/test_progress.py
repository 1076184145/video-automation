from __future__ import annotations

import subprocess
import sys
import time
import unittest

from video_automation.progress import MAX_CAPTURED_STDERR_CHARS, run_ffmpeg_with_progress


class ProgressRunnerTests(unittest.TestCase):
    def test_timeout_does_not_depend_on_stderr_activity(self) -> None:
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            run_ffmpeg_with_progress(
                [sys.executable, "-c", "import time; time.sleep(1)"],
                duration_seconds=1.0,
                timeout=0.1,
            )
        self.assertLess(time.monotonic() - started, 0.8)

    def test_captured_stderr_is_bounded(self) -> None:
        result = run_ffmpeg_with_progress(
            [
                sys.executable,
                "-c",
                f"import sys; sys.stderr.write('x' * {MAX_CAPTURED_STDERR_CHARS * 2})",
            ],
            duration_seconds=1.0,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0)
        self.assertLessEqual(len(result.stderr), MAX_CAPTURED_STDERR_CHARS)


if __name__ == "__main__":
    unittest.main()
