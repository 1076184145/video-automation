from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import Settings
from .io_utils import read_json_file
from .process_tree import attach_process_tree, process_group_popen_kwargs, terminate_process_tree
from .task_queue import QueueControlRequested
from .transcribe_worker import WorkerInfrastructureError


MAX_TRANSCRIPTION_PROCESS_OUTPUT_BYTES = 256 * 1024


def _run_transcription_process(
    command: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    timeout: float,
    heartbeat_path: Path | None = None,
    no_progress_timeout: float | None = None,
    control_callback: Callable[[], str | None] | None = None,
) -> subprocess.CompletedProcess[str]:
    initial_action = control_callback() if control_callback else None
    if initial_action in {"paused", "canceled"}:
        raise QueueControlRequested(initial_action)
    deadline = time.monotonic() + max(0.01, float(timeout))
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=stdout_file,
            stderr=stderr_file,
            **process_group_popen_kwargs(),
        )
        attach_process_tree(process)
        last_progress_at = time.monotonic()
        last_heartbeat_mtime_ns = 0
        last_phase = "process_started"
        try:
            while process.poll() is None:
                action = control_callback() if control_callback else None
                if action in {"paused", "canceled"}:
                    raise QueueControlRequested(action)
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(command, timeout)
                if heartbeat_path is not None:
                    try:
                        heartbeat_mtime_ns = heartbeat_path.stat().st_mtime_ns
                    except OSError:
                        heartbeat_mtime_ns = 0
                    if heartbeat_mtime_ns and heartbeat_mtime_ns != last_heartbeat_mtime_ns:
                        last_heartbeat_mtime_ns = heartbeat_mtime_ns
                        last_progress_at = time.monotonic()
                        heartbeat = read_json_file(heartbeat_path)
                        if isinstance(heartbeat, dict):
                            last_phase = str(heartbeat.get("phase") or last_phase)
                    if no_progress_timeout is not None:
                        stalled_for = time.monotonic() - last_progress_at
                        if stalled_for >= max(0.01, float(no_progress_timeout)):
                            raise WorkerInfrastructureError(
                                "transcription subprocess made no progress "
                                f"for {stalled_for:.1f}s (last phase: {last_phase})"
                            )
                time.sleep(0.1)
        except BaseException:
            terminate_process_tree(process)
            raise
        return subprocess.CompletedProcess(
            command,
            process.returncode or 0,
            _read_transcription_output(stdout_file),
            _read_transcription_output(stderr_file),
        )


def _read_transcription_output(handle: Any) -> str:
    handle.flush()
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    handle.seek(max(0, size - MAX_TRANSCRIPTION_PROCESS_OUTPUT_BYTES))
    return handle.read().decode("utf-8", errors="replace")


def _remove_partial_transcripts(job_dir: Path) -> None:
    for name in ("transcript.txt", "transcript.srt", "transcript.json"):
        try:
            (job_dir / name).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _transcript_outputs_complete(job_dir: Path) -> bool:
    txt_path = job_dir / "transcript.txt"
    srt_path = job_dir / "transcript.srt"
    payload = read_json_file(job_dir / "transcript.json")
    return (
        txt_path.is_file()
        and srt_path.is_file()
        and isinstance(payload, dict)
        and isinstance(payload.get("segments"), list)
    )


def _project_python(settings: Settings) -> Path:
    candidates = [
        settings.root / "venv" / "Scripts" / "python.exe",
        settings.root / ".venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return Path(sys.executable)


def _ensure_faster_whisper_cuda_ready(settings: Settings) -> None:
    if settings.faster_whisper_device.strip().lower() != "cuda":
        return
    if shutil.which("nvidia-smi") is None:
        raise RuntimeError("FASTER_WHISPER_DEVICE=cuda requires NVIDIA driver/nvidia-smi, but nvidia-smi was not found")
    try:
        import ctranslate2
    except ImportError as exc:
        raise RuntimeError("FASTER_WHISPER_DEVICE=cuda requires ctranslate2 from faster-whisper") from exc
    get_count = getattr(ctranslate2, "get_cuda_device_count", None)
    if callable(get_count) and get_count() <= 0:
        raise RuntimeError("FASTER_WHISPER_DEVICE=cuda is configured, but CTranslate2 reports no CUDA devices")


def _ensure_funasr_cuda_ready(settings: Settings) -> None:
    if not settings.funasr_device.strip().lower().startswith("cuda"):
        return
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("FUNASR_DEVICE=cuda requires torch with CUDA support") from exc
    if not torch.cuda.is_available():
        raise RuntimeError("FUNASR_DEVICE=cuda is configured, but torch.cuda.is_available() is false")


def _transcribe_timeout(settings: Settings, audio_path: Path) -> int:
    duration = _wav_duration_seconds(audio_path)
    minimum = int(getattr(settings, "whisper_timeout_min_seconds", 300))
    multiplier = float(getattr(settings, "whisper_timeout_multiplier", 10.0))
    return max(minimum, int(duration * multiplier))


def _backend_attempt_timeout(settings: Settings, audio_path: Path) -> int:
    legacy_timeout = _transcribe_timeout(settings, audio_path)
    hard_limit = max(30, int(getattr(settings, "transcribe_attempt_timeout_seconds", 1800)))
    return min(legacy_timeout, hard_limit)


def _wav_duration_seconds(audio_path: Path) -> float:
    try:
        with wave.open(str(audio_path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
            return frames / float(rate or 1)
    except (OSError, wave.Error):
        return 0.0


def _language_code(language: str) -> str | None:
    value = language.strip().lower()
    if not value or value == "auto":
        return None
    if value in {"chinese", "zh", "zh-cn", "cn"}:
        return "zh"
    return value
