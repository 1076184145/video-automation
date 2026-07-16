from __future__ import annotations

import threading
import time
import itertools
from contextlib import contextmanager
from collections.abc import Callable, Iterator
from typing import Any

from .task_queue import QueueControlRequested


class ResourceWaitTimeout(TimeoutError):
    """Raised when a task cannot acquire a bounded execution resource."""


class ExecutionGate:
    def __init__(self, capacity: int = 1) -> None:
        self._capacity = max(1, int(capacity))
        self._semaphore = threading.Semaphore(self._capacity)
        self._state_lock = threading.Lock()
        self._holders: dict[int, dict[str, Any]] = {}
        self._waiters = 0
        self._tokens = itertools.count(1)

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


GPU_EXECUTION_GATE = ExecutionGate(1)


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
