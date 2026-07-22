from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from video_automation.progress import MAX_CAPTURED_STDERR_CHARS, run_ffmpeg_with_progress
from video_automation.task_queue import QueueControlRequested


class ProgressRunnerTests(unittest.TestCase):
    def test_preexisting_cancel_does_not_spawn_a_child(self) -> None:
        with patch("video_automation.progress.subprocess.Popen") as popen:
            with self.assertRaisesRegex(QueueControlRequested, "canceled"):
                run_ffmpeg_with_progress(
                    ["unused"],
                    duration_seconds=1.0,
                    control_callback=lambda: "canceled",
                )
        popen.assert_not_called()

    def test_control_callback_interrupts_silent_child(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ready_path = Path(temp_dir) / "ready"
            cancel_requested_at: float | None = None

            def control() -> str | None:
                nonlocal cancel_requested_at
                if not ready_path.is_file():
                    return None
                cancel_requested_at = cancel_requested_at or time.monotonic()
                return "canceled"

            with self.assertRaisesRegex(QueueControlRequested, "canceled"):
                run_ffmpeg_with_progress(
                    [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; import sys, time; "
                        "Path(sys.argv[1]).write_text('ready'); time.sleep(10)",
                        str(ready_path),
                    ],
                    duration_seconds=1.0,
                    control_callback=control,
                    timeout=30,
                )
            self.assertIsNotNone(cancel_requested_at)
            self.assertLess(time.monotonic() - float(cancel_requested_at), 1.5)

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
