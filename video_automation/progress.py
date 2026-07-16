from __future__ import annotations

import re
import queue
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from .task_queue import QueueControlRequested


ProgressCallback = Callable[[float], None]
ControlCallback = Callable[[], str | None]
FFMPEG_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")
MAX_CAPTURED_STDERR_CHARS = 256 * 1024
STDERR_QUEUE_LINES = 128


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def run_ffmpeg_with_progress(
    command: list[str],
    *,
    duration_seconds: float,
    progress_callback: ProgressCallback | None = None,
    control_callback: ControlCallback | None = None,
    timeout: float | None = None,
) -> CommandResult:
    started_at = time.monotonic()
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stderr_parts: deque[str] = deque()
    stderr_chars = 0
    lines: queue.Queue[str | object] = queue.Queue(maxsize=STDERR_QUEUE_LINES)
    sentinel = object()
    stop_reader = threading.Event()

    def enqueue(value: str | object) -> bool:
        while not stop_reader.is_set():
            try:
                lines.put(value, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def read_stderr() -> None:
        assert process.stderr is not None
        try:
            for line in process.stderr:
                if not enqueue(line):
                    return
        finally:
            enqueue(sentinel)

    reader = threading.Thread(target=read_stderr, daemon=True)
    reader.start()
    last_percent = -1
    try:
        while True:
            action = control_callback() if control_callback else None
            if action in {"paused", "canceled"}:
                raise QueueControlRequested(action)
            remaining = None if timeout is None else timeout - (time.monotonic() - started_at)
            if remaining is not None and remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            wait_seconds = 0.1 if remaining is None else max(0.001, min(0.1, remaining))
            try:
                item = lines.get(timeout=wait_seconds)
            except queue.Empty:
                if process.poll() is not None and not reader.is_alive():
                    break
                continue
            if item is sentinel:
                break
            line = str(item)
            stderr_parts.append(line)
            stderr_chars += len(line)
            while stderr_parts and stderr_chars > MAX_CAPTURED_STDERR_CHARS:
                removed = stderr_parts.popleft()
                stderr_chars -= len(removed)
            percent = progress_percent_from_line(line, duration_seconds)
            if percent is None or progress_callback is None:
                continue
            integer_percent = int(percent)
            if integer_percent <= last_percent:
                continue
            last_percent = integer_percent
            progress_callback(round(percent, 2))
        process.wait(timeout=5)
    except Exception:
        stop_reader.set()
        process.kill()
        process.wait()
        reader.join(timeout=1)
        if process.stderr is not None:
            process.stderr.close()
        raise
    stop_reader.set()
    reader.join(timeout=1)
    if process.stderr is not None:
        process.stderr.close()
    if progress_callback is not None and process.returncode == 0:
        progress_callback(100.0)
    stderr = "".join(stderr_parts)
    if len(stderr) > MAX_CAPTURED_STDERR_CHARS:
        stderr = stderr[-MAX_CAPTURED_STDERR_CHARS:]
    return CommandResult(process.returncode or 0, "", stderr)


def progress_percent_from_line(line: str, duration_seconds: float) -> float | None:
    if duration_seconds <= 0:
        return None
    match = FFMPEG_TIME_RE.search(line)
    if not match:
        return None
    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    current = hours * 3600 + minutes * 60 + seconds
    return max(0.0, min(100.0, current / duration_seconds * 100))
