from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from video_automation.queue_worker import QueueWorkerProcess, process_is_alive, recover_stale_queue_items


class FakeProcess:
    def __init__(self) -> None:
        self.pid = 4321
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


class QueueWorkerProcessTests(unittest.TestCase):
    def test_worker_process_uses_project_root_and_stops_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = SimpleNamespace(
                root=root,
                logs_dir=root / "logs",
                api_parallel_jobs=2,
            )
            calls = []
            process = FakeProcess()

            def factory(command, **kwargs):
                calls.append((command, kwargs))
                return process

            worker = QueueWorkerProcess(settings, owner_pid=9876, process_factory=factory)  # type: ignore[arg-type]
            worker.start()
            self.assertTrue(worker.is_running)
            self.assertEqual(worker.pid, 4321)
            self.assertEqual(worker.status()["workers"], 2)
            command, kwargs = calls[0]
            self.assertIn("video_automation.queue_worker", command)
            self.assertEqual(command[-6:-2], ["--workers", "2", "--owner-pid", "9876"])
            self.assertEqual(command[-2], "--pid-file")
            self.assertTrue(command[-1].endswith("queue_worker_9876.json"))
            self.assertEqual(kwargs["cwd"], str(root))
            self.assertEqual(kwargs["env"]["VIDEO_AUTOMATION_ROOT"], str(root))

            with patch("video_automation.queue_worker.terminate_process_tree") as terminate_tree:
                worker.stop()
            terminate_tree.assert_called_once_with(process)
            self.assertFalse(worker.is_running)

    def test_owner_liveness_check_detects_missing_parent(self) -> None:
        self.assertTrue(process_is_alive(os.getpid()))
        self.assertFalse(process_is_alive(2_147_483_647))

    def test_launcher_exit_does_not_restart_while_reported_worker_pid_is_alive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = SimpleNamespace(root=root, logs_dir=root / "logs", api_parallel_jobs=1)
            launcher = FakeProcess()
            worker = QueueWorkerProcess(
                settings,
                owner_pid=1234,
                process_factory=lambda *_args, **_kwargs: launcher,
            )  # type: ignore[arg-type]
            try:
                worker.start()
                worker._worker_pid_path.write_text(
                    '{"pid": %d, "owner_pid": 1234}' % os.getpid(),
                    encoding="utf-8",
                )
                launcher.returncode = 0
                time.sleep(1.2)
                self.assertTrue(worker.is_running)
                self.assertEqual(worker.pid, os.getpid())
                self.assertEqual(worker.restart_count, 0)
            finally:
                worker.stop()

    def test_crashed_worker_is_restarted_and_exit_code_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = SimpleNamespace(root=root, logs_dir=root / "logs", api_parallel_jobs=1)
            first = FakeProcess()
            second = FakeProcess()
            processes = iter([first, second])
            worker = QueueWorkerProcess(settings, process_factory=lambda *_args, **_kwargs: next(processes))  # type: ignore[arg-type]
            try:
                worker.start()
                first.returncode = -1073741819
                deadline = time.monotonic() + 3
                while (worker.restart_count < 1 or not worker.is_running) and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertEqual(worker.status()["last_exit_code"], -1073741819)
                self.assertTrue(worker.is_running)
                self.assertIn("exited unexpectedly", (root / "logs" / "queue_worker.log").read_text(encoding="utf-8"))
            finally:
                worker.stop()

    def test_rapid_restart_circuit_prevents_worker_storm(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = SimpleNamespace(root=root, logs_dir=root / "logs", api_parallel_jobs=1)
            processes = [FakeProcess(), FakeProcess(), FakeProcess()]
            iterator = iter(processes)
            worker = QueueWorkerProcess(
                settings,
                max_rapid_restarts=1,
                process_factory=lambda *_args, **_kwargs: next(iterator),
            )  # type: ignore[arg-type]
            try:
                worker.start()
                processes[0].returncode = 1
                deadline = time.monotonic() + 3
                while worker.restart_count < 1 and time.monotonic() < deadline:
                    time.sleep(0.05)
                processes[1].returncode = 1
                deadline = time.monotonic() + 3
                while not worker.circuit_open and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(worker.circuit_open)
                self.assertFalse(worker.is_running)
                self.assertEqual(worker.restart_count, 1)
            finally:
                worker.stop()

    def test_periodic_recovery_uses_a_bounded_stale_cutoff(self) -> None:
        cutoffs = []
        repository = SimpleNamespace(recover_interrupted=lambda cutoff: cutoffs.append(cutoff) or 2)
        recovered = recover_stale_queue_items(
            repository,
            stale_seconds=30,
            now=datetime.fromisoformat("2026-07-15T12:00:00"),
        )
        self.assertEqual(recovered, 2)
        self.assertEqual(cutoffs, ["2026-07-15T11:59:30"])


if __name__ == "__main__":
    unittest.main()
