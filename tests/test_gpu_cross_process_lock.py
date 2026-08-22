"""Tests for the cross-process GPU file lock."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from video_automation import resources
from video_automation.resources import (
    CrossProcessFileLock,
    ExecutionGate,
    ResourceWaitTimeout,
)
from video_automation.task_queue import QueueControlRequested


class CrossProcessFileLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lock_path = Path(self._tmp.name) / "gpu.lock"
        self.lock = CrossProcessFileLock(self.lock_path, poll_interval_seconds=0.02)

    def test_acquire_and_release_round_trip(self) -> None:
        self.assertFalse(self.lock.acquire(owner="test", max_wait_seconds=2))
        self.assertTrue(self.lock.path.exists())
        self.lock.release()
        # Immediately reusable after release.
        self.assertFalse(self.lock.acquire(owner="test", max_wait_seconds=2))
        self.lock.release()

    def test_second_lock_instance_waits_then_succeeds(self) -> None:
        holder = CrossProcessFileLock(self.lock_path, poll_interval_seconds=0.02)
        contender = CrossProcessFileLock(self.lock_path, poll_interval_seconds=0.02)
        self.assertFalse(holder.acquire(owner="holder", max_wait_seconds=2))
        waited: list[bool] = []

        def contend() -> None:
            waited.append(contender.acquire(owner="contender", max_wait_seconds=5))
            contender.release()

        thread = threading.Thread(target=contend)
        thread.start()
        threading.Event().wait(0.1)
        holder.release()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(waited, [True])

    def test_timeout_raises_resource_wait_timeout(self) -> None:
        holder = CrossProcessFileLock(self.lock_path, poll_interval_seconds=0.02)
        self.assertFalse(holder.acquire(owner="holder", max_wait_seconds=2))
        contender = CrossProcessFileLock(self.lock_path, poll_interval_seconds=0.02)
        with self.assertRaises(ResourceWaitTimeout):
            contender.acquire(owner="contender", max_wait_seconds=0.1)
        holder.release()

    def test_cancel_while_waiting_raises_control_request(self) -> None:
        holder = CrossProcessFileLock(self.lock_path, poll_interval_seconds=0.02)
        self.assertFalse(holder.acquire(owner="holder", max_wait_seconds=2))
        contender = CrossProcessFileLock(self.lock_path, poll_interval_seconds=0.02)
        calls = {"count": 0}

        def control() -> str | None:
            calls["count"] += 1
            return "canceled" if calls["count"] > 1 else None

        with self.assertRaises(QueueControlRequested):
            contender.acquire(owner="contender", control_callback=control, max_wait_seconds=5)
        holder.release()

    def test_double_acquire_is_rejected(self) -> None:
        self.lock.acquire(owner="test", max_wait_seconds=2)
        self.addCleanup(self.lock.release)
        with self.assertRaises(RuntimeError):
            self.lock.acquire(owner="test", max_wait_seconds=2)

    def test_release_without_acquire_is_noop(self) -> None:
        self.lock.release()


class GpuFileLockResolverTests(unittest.TestCase):
    def test_env_disable_returns_none(self) -> None:
        import os

        original = resources._GPU_FILE_LOCK_CACHE
        resources._GPU_FILE_LOCK_CACHE = None
        os.environ["GPU_CROSS_PROCESS_LOCK_ENABLED"] = "0"
        try:
            self.assertIsNone(resources.gpu_file_lock())
        finally:
            os.environ.pop("GPU_CROSS_PROCESS_LOCK_ENABLED", None)
            resources._GPU_FILE_LOCK_CACHE = original

    def test_env_override_uses_custom_path(self) -> None:
        import os

        with tempfile.TemporaryDirectory() as tmp:
            custom = Path(tmp) / "custom-gpu.lock"
            original = resources._GPU_FILE_LOCK_CACHE
            resources._GPU_FILE_LOCK_CACHE = None
            os.environ["GPU_LOCK_PATH"] = str(custom)
            try:
                lock = resources.gpu_file_lock()
            finally:
                os.environ.pop("GPU_LOCK_PATH", None)
                resources._GPU_FILE_LOCK_CACHE = original
            self.assertIsNotNone(lock)
            assert lock is not None
            self.assertEqual(lock.path, custom)


class GateWithFileLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lock_path = Path(self._tmp.name) / "gpu.lock"
        self.lock = CrossProcessFileLock(self.lock_path, poll_interval_seconds=0.02)

    def test_gate_holds_file_lock_only_inside_slot(self) -> None:
        gate = ExecutionGate(1, file_lock_factory=lambda: self.lock)

        with gate.slot(enabled=True, owner="render", max_wait_seconds=2) as waited:
            self.assertFalse(waited)
            # The cross-process lock is held while the slot is active.
            contender = CrossProcessFileLock(self.lock_path, poll_interval_seconds=0.02)
            with self.assertRaises(ResourceWaitTimeout):
                contender.acquire(owner="other-process", max_wait_seconds=0.1)

        # Released with the slot: a fresh contender can take it now.
        contender = CrossProcessFileLock(self.lock_path, poll_interval_seconds=0.02)
        self.assertFalse(contender.acquire(owner="other-process", max_wait_seconds=2))
        contender.release()

    def test_gate_disabled_slot_skips_file_lock(self) -> None:
        gate = ExecutionGate(1, file_lock_factory=lambda: self.lock)
        with gate.slot(enabled=False) as waited:
            self.assertFalse(waited)

    def test_gate_file_lock_failure_releases_semaphore(self) -> None:
        holder = CrossProcessFileLock(self.lock_path, poll_interval_seconds=0.02)
        self.assertFalse(holder.acquire(owner="other-process", max_wait_seconds=2))
        gate = ExecutionGate(1, file_lock_factory=lambda: self.lock)
        with self.assertRaises(ResourceWaitTimeout):
            with gate.slot(enabled=True, owner="render", max_wait_seconds=0.1):
                pass
        holder.release()
        # The in-process semaphore must be free again despite the failure.
        with gate.slot(enabled=True, owner="render", max_wait_seconds=2):
            pass


if __name__ == "__main__":
    unittest.main()
