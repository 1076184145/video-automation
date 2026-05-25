from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from typing import Callable


ProgressCallback = Callable[[float], None]
FFMPEG_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


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
    timeout: int | None = None,
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
    stderr_parts: list[str] = []
    last_percent = -1
    try:
        assert process.stderr is not None
        for line in process.stderr:
            stderr_parts.append(line)
            if timeout is not None and time.monotonic() - started_at > timeout:
                process.kill()
                raise subprocess.TimeoutExpired(command, timeout)
            percent = progress_percent_from_line(line, duration_seconds)
            if percent is None or progress_callback is None:
                continue
            integer_percent = int(percent)
            if integer_percent <= last_percent:
                continue
            last_percent = integer_percent
            progress_callback(round(percent, 2))
        _, stderr_tail = process.communicate(timeout=5)
        if stderr_tail:
            stderr_parts.append(stderr_tail)
    except Exception:
        process.kill()
        process.communicate()
        raise
    if progress_callback is not None and process.returncode == 0:
        progress_callback(100.0)
    return CommandResult(process.returncode or 0, "", "".join(stderr_parts))


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
