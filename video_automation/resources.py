from __future__ import annotations

import os
import sys
import threading
import time
import itertools
from contextlib import contextmanager
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from .task_queue import QueueControlRequested


class ResourceWaitTimeout(TimeoutError):
    """Raised when a task cannot acquire a bounded execution resource."""


def _project_root() -> Path:
    override = os.environ.get("VIDEO_AUTOMATION_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


class CrossProcessFileLock:
    """Advisory mutex that also excludes other OS processes.

    Uses msvcrt.locking on Windows and fcntl.flock elsewhere. The kernel
    releases the lock when the owning process dies, so a crash can never
    leave a stale lock behind.
    """

    def __init__(self, path: Path, *, poll_interval_seconds: float = 0.2) -> None:
        self._path = Path(path)
        self._interval = max(0.02, float(poll_interval_seconds))
        self._fd: int | None = None

    @property
    def path(self) -> Path:
        return self._path

    def acquire(
        self,
        *,
        owner: str = "",
        control_callback: Callable[[], str | None] | None = None,
        max_wait_seconds: float | None = None,
        on_wait: Callable[[], None] | None = None,
        poll_interval_seconds: float | None = None,
    ) -> bool:
        """Block until the cross-process lock is held; returns True if it waited."""
        if self._fd is not None:
            raise RuntimeError("CrossProcessFileLock acquired twice")
        interval = self._interval if poll_interval_seconds is None else max(0.02, float(poll_interval_seconds))
        started_at = time.monotonic()
        waited = False
        notified = False
        while True:
            fd = self._open_lock_file()
            locked = False
            try:
                locked = self._try_lock(fd)
                if locked:
                    payload = f"{os.getpid()}\n".encode("utf-8")
                    os.lseek(fd, 0, os.SEEK_SET)
                    os.write(fd, payload)
                    os.ftruncate(fd, len(payload))
            except OSError:
                os.close(fd)
                raise
            if locked:
                self._fd = fd
                return waited
            os.close(fd)
            waited = True
            if not notified and on_wait is not None:
                on_wait()
                notified = True
            action = control_callback() if control_callback else None
            if action in {"paused", "canceled"}:
                raise QueueControlRequested(action)
            if max_wait_seconds is not None:
                remaining = started_at + max(0.0, float(max_wait_seconds)) - time.monotonic()
                if remaining <= 0:
                    label = owner.strip() or "resource task"
                    raise ResourceWaitTimeout(
                        f"Timed out waiting {max_wait_seconds:g}s for cross-process lock {self._path} ({label})"
                    )
            time.sleep(min(interval, 5.0))

    def release(self) -> None:
        fd = self._fd
        self._fd = None
        if fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            os.close(fd)

    def _open_lock_file(self) -> int:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            # Ensure byte 0 exists so msvcrt.locking always has a region to lock.
            os.ftruncate(fd, 1)
        except OSError:
            pass
        return fd

    def _try_lock(self, fd: int) -> bool:
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True


_GPU_FILE_LOCK_CACHE: list[CrossProcessFileLock | None] | None = None


def gpu_file_lock() -> CrossProcessFileLock | None:
    """Cross-process GPU mutex shared by the API server and worker processes.

    The API process (covers, local LLM) and the queue-worker process
    (transcription, rendering) each own an ExecutionGate, but those gates only
    exclude threads inside one process. This file lock closes that gap.
    """
    global _GPU_FILE_LOCK_CACHE
    if _GPU_FILE_LOCK_CACHE is None:
        raw = os.environ.get("GPU_CROSS_PROCESS_LOCK_ENABLED", "1").strip().lower()
        if raw in {"0", "false", "no", "off"}:
            _GPU_FILE_LOCK_CACHE = [None]
        else:
            override = os.environ.get("GPU_LOCK_PATH", "").strip()
            path = Path(override).expanduser() if override else _project_root() / "logs-runtime" / "gpu.lock"
            _GPU_FILE_LOCK_CACHE = [CrossProcessFileLock(path)]
    return _GPU_FILE_LOCK_CACHE[0]


class ExecutionGate:
    def __init__(
        self,
        capacity: int = 1,
        *,
        file_lock_factory: Callable[[], CrossProcessFileLock | None] | None = None,
    ) -> None:
        self._capacity = max(1, int(capacity))
        self._semaphore = threading.Semaphore(self._capacity)
        self._state_lock = threading.Lock()
        self._holders: dict[int, dict[str, Any]] = {}
        self._waiters = 0
        self._tokens = itertools.count(1)
        self._file_lock_factory = file_lock_factory

    @contextmanager
    def slot(
        self,
        *,
        enabled: bool = True,
        on_wait: Callable[[], None] | None = None,
        on_acquired: Callable[[], None] | None = None,
        control_callback: Callable[[], str | None] | None = None,
        max_wait_seconds: float | None = None,
        poll_interval_seconds: float = 0.1,
        owner: str = "",
    ) -> Iterator[bool]:
        if not enabled:
            yield False
            return

        action = control_callback() if control_callback else None
        if action in {"paused", "canceled"}:
            raise QueueControlRequested(action)

        acquired_immediately = self._semaphore.acquire(blocking=False)
        waited = not acquired_immediately
        if waited:
            if on_wait is not None:
                on_wait()
            started_at = time.monotonic()
            deadline = None if max_wait_seconds is None else started_at + max(0.0, float(max_wait_seconds))
            with self._state_lock:
                self._waiters += 1
            try:
                interval = max(0.01, float(poll_interval_seconds))
                while True:
                    action = control_callback() if control_callback else None
                    if action in {"paused", "canceled"}:
                        raise QueueControlRequested(action)
                    remaining = None if deadline is None else deadline - time.monotonic()
                    if remaining is not None and remaining <= 0:
                        label = owner.strip() or "resource task"
                        raise ResourceWaitTimeout(
                            f"Timed out waiting {max_wait_seconds:g}s for execution slot ({label})"
                        )
                    wait_for = interval if remaining is None else min(interval, remaining)
                    if self._semaphore.acquire(timeout=wait_for):
                        break
            finally:
                with self._state_lock:
                    self._waiters -= 1
        token = next(self._tokens)
        file_lock = None
        try:
            if self._file_lock_factory is not None:
                candidate = self._file_lock_factory()
                if candidate is not None:
                    file_waited = candidate.acquire(
                        owner=owner,
                        control_callback=control_callback,
                        max_wait_seconds=max_wait_seconds,
                        on_wait=on_wait,
                        poll_interval_seconds=poll_interval_seconds,
                    )
                    waited = waited or file_waited
                    file_lock = candidate
            with self._state_lock:
                self._holders[token] = {
                    "owner": owner.strip() or None,
                    "thread_id": threading.get_ident(),
                    "acquired_at_monotonic": time.monotonic(),
                }
            try:
                if waited and on_acquired is not None:
                    on_acquired()
                yield waited
            finally:
                with self._state_lock:
                    self._holders.pop(token, None)
        finally:
            if file_lock is not None:
                file_lock.release()
            self._semaphore.release()

    def snapshot(self) -> dict[str, Any]:
        """Return a diagnostic snapshot without exposing synchronization internals."""
        now = time.monotonic()
        with self._state_lock:
            holders = [
                {
                    "owner": item["owner"],
                    "thread_id": item["thread_id"],
                    "held_seconds": round(max(0.0, now - item["acquired_at_monotonic"]), 3),
                }
                for item in self._holders.values()
            ]
            return {
                "capacity": self._capacity,
                "in_use": len(holders),
                "waiters": self._waiters,
                "holders": holders,
            }


GPU_EXECUTION_GATE = ExecutionGate(1, file_lock_factory=gpu_file_lock)


def transcription_uses_gpu(settings: Any) -> bool:
    backend = str(getattr(settings, "whisper_backend", "") or "").strip().lower()
    faster_device = str(getattr(settings, "faster_whisper_device", "") or "").strip().lower()
    funasr_device = str(getattr(settings, "funasr_device", "") or "").strip().lower()

    faster_enabled = backend in {"faster-whisper", "funasr-whisper", "funasr-faster-whisper"}
    funasr_enabled = backend in {"funasr", "funasr-whisper", "funasr-faster-whisper"}
    return (faster_enabled and faster_device.startswith("cuda")) or (
        funasr_enabled and funasr_device.startswith("cuda")
    )


def rendering_uses_gpu(settings: Any) -> bool:
    encoder = str(getattr(settings, "render_video_encoder", "") or "").strip().lower()
    return encoder in {"h264_nvenc", "nvenc"}


def job_gpu_status_callbacks(job: Any, task_label: str) -> tuple[Callable[[], None], Callable[[], None]]:
    label = task_label.strip() or "GPU task"

    def update(message: str) -> None:
        job.update_stage_progress(getattr(job, "stage_progress", None), message=message)

    return (
        lambda: update(f"Waiting for GPU to start {label}."),
        lambda: update(f"GPU available. Starting {label}."),
    )
