from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from video_automation.api_system import run_tools_install


class FakeInstallProcess:
    def __init__(self, *, stdout=None, returncode: int | None = 1) -> None:
        self.pid = 4321
        self.stdout = stdout
        self.returncode = returncode
        self.wait_called = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_called = True
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


class FailingStdout:
    def __init__(self) -> None:
        self.closed = False

    def __iter__(self):
        raise OSError("pipe failed")

    def close(self) -> None:
        self.closed = True


class ToolsInstallProcessTests(unittest.TestCase):
    def test_install_process_is_attached_and_released_after_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = SimpleNamespace(root=Path(temp_dir))
            stdout = io.StringIO("installing\n")
            process = FakeInstallProcess(stdout=stdout, returncode=1)
            with (
                patch("video_automation.api_system.subprocess.Popen", return_value=process) as popen,
                patch(
                    "video_automation.api_system.process_group_popen_kwargs",
                    return_value={"creationflags": 123},
                ),
                patch("video_automation.api_system.attach_process_tree") as attach,
                patch("video_automation.api_system.release_process_tree") as release,
                patch("video_automation.api_system.terminate_process_tree") as terminate,
                patch("video_automation.api_system.set_tools_install_state") as set_state,
            ):
                run_tools_install(settings, ["installer"])

            self.assertEqual(popen.call_args.kwargs["creationflags"], 123)
            attach.assert_called_once_with(process)
            release.assert_called_once_with(process)
            terminate.assert_not_called()
            self.assertTrue(process.wait_called)
            self.assertTrue(stdout.closed)
            self.assertEqual(set_state.call_args.kwargs["status"], "failed")
            self.assertEqual(set_state.call_args.kwargs["returncode"], 1)

    def test_install_stream_error_terminates_process_tree_and_marks_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = SimpleNamespace(root=Path(temp_dir))
            stdout = FailingStdout()
            process = FakeInstallProcess(stdout=stdout, returncode=None)
            with (
                patch("video_automation.api_system.subprocess.Popen", return_value=process),
                patch("video_automation.api_system.process_group_popen_kwargs", return_value={}),
                patch("video_automation.api_system.attach_process_tree"),
                patch("video_automation.api_system.release_process_tree") as release,
                patch("video_automation.api_system.terminate_process_tree") as terminate,
                patch("video_automation.api_system.set_tools_install_state") as set_state,
            ):
                run_tools_install(settings, ["installer"])

            terminate.assert_called_once_with(process)
            release.assert_called_once_with(process)
            self.assertFalse(process.wait_called)
            self.assertTrue(stdout.closed)
            self.assertEqual(set_state.call_args.kwargs["status"], "failed")
            self.assertIn("pipe failed", set_state.call_args.kwargs["message"])


if __name__ == "__main__":
    unittest.main()
