from __future__ import annotations

import threading
from contextlib import contextmanager
from collections.abc import Callable, Iterator
from typing import Any


class ExecutionGate:
    def __init__(self, capacity: int = 1) -> None:
        self._semaphore = threading.Semaphore(max(1, int(capacity)))

    @contextmanager
    def slot(
        self,
        *,
        enabled: bool = True,
        on_wait: Callable[[], None] | None = None,
        on_acquired: Callable[[], None] | None = None,
    ) -> Iterator[bool]:
        if not enabled:
            yield False
            return

        acquired_immediately = self._semaphore.acquire(blocking=False)
        waited = not acquired_immediately
        if waited:
            if on_wait is not None:
                on_wait()
            self._semaphore.acquire()
        try:
            if waited and on_acquired is not None:
                on_acquired()
            yield waited
        finally:
            self._semaphore.release()


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
