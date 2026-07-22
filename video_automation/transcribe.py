from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .io_utils import read_json_file, valid_json_file, write_json_atomic, write_text_atomic
from .profanity import apply_replacements, censor_text, censor_transcript_payload
from .resources import GPU_EXECUTION_GATE, transcription_uses_gpu
from .task_queue import QueueControlRequested
from .transcribe_runtime import (
    _backend_attempt_timeout,
    _ensure_faster_whisper_cuda_ready,
    _ensure_funasr_cuda_ready,
    _language_code,
    _project_python,
    _remove_partial_transcripts,
    _run_transcription_process,
    _transcribe_timeout,
    _transcript_outputs_complete,
    _wav_duration_seconds,
)
from .transcribe_worker import (
    PersistentTranscriptionWorker,
    TranscriptionTaskError,
    WorkerInfrastructureError,
)


_FUNASR_PERSISTENT_WORKER = PersistentTranscriptionWorker()
_BACKEND_CIRCUIT_LOCK = threading.Lock()
_BACKEND_UNHEALTHY_UNTIL: dict[str, float] = {}


def transcribe_audio(
    settings: Settings,
    audio_path: Path,
    job_dir: Path,
    *,
    force: bool = False,
    resource_wait_callback: Callable[[], None] | None = None,
    resource_acquired_callback: Callable[[], None] | None = None,
    control_callback: Callable[[], str | None] | None = None,
) -> None:
    txt_path = job_dir / "transcript.txt"
    srt_path = job_dir / "transcript.srt"
    json_path = job_dir / "transcript.json"
    if txt_path.exists() and srt_path.exists() and valid_json_file(json_path) and not force:
        return
    _write_transcription_settings_snapshot(settings, job_dir)
    with GPU_EXECUTION_GATE.slot(
        enabled=transcription_uses_gpu(settings),
        on_wait=resource_wait_callback,
        on_acquired=resource_acquired_callback,
        control_callback=control_callback,
        max_wait_seconds=_transcribe_timeout(settings, audio_path),
        owner=f"transcription:{job_dir.name}",
    ):
        _transcribe_audio_unlocked(
            settings,
            audio_path,
            job_dir,
            force=force,
            control_callback=control_callback,
        )


def _transcribe_audio_unlocked(
    settings: Settings,
    audio_path: Path,
    job_dir: Path,
    *,
    force: bool = False,
    control_callback: Callable[[], str | None] | None = None,
) -> None:
    txt_path = job_dir / "transcript.txt"
    srt_path = job_dir / "transcript.srt"
    json_path = job_dir / "transcript.json"
    if txt_path.exists() and srt_path.exists() and valid_json_file(json_path) and not force:
        return
    if settings.whisper_backend in {"funasr-whisper", "funasr-faster-whisper"}:
        _run_funasr_with_whisper_fallback(
            settings,
            audio_path,
            job_dir,
            txt_path,
            srt_path,
            json_path,
            control_callback=control_callback,
        )
        return
    if settings.whisper_backend == "faster-whisper":
        if os.environ.get("VIDEO_AUTOMATION_TRANSCRIBE_CHILD") == "1":
            transcribe_audio_faster_whisper(settings, audio_path, txt_path, srt_path, json_path)
        else:
            _run_faster_whisper_subprocess(settings, audio_path, job_dir, control_callback=control_callback)
        return
    if settings.whisper_backend == "funasr":
        if os.environ.get("VIDEO_AUTOMATION_TRANSCRIBE_CHILD") == "1":
            transcribe_audio_funasr(settings, audio_path, txt_path, srt_path, json_path)
        else:
            _run_funasr_subprocess(settings, audio_path, job_dir, control_callback=control_callback)
        return
    if settings.whisper_backend == "whisperx":
        raise RuntimeError("WHISPER_BACKEND=whisperx is reserved for a later phase; use faster-whisper for now")
    if settings.whisper_backend != "cli":
        raise RuntimeError(f"unsupported WHISPER_BACKEND: {settings.whisper_backend}")

    work_dir = job_dir / "_whisper"
    if force and work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    command = [
        str(settings.whisper_bin),
        str(audio_path),
        "--model",
        settings.whisper_model,
        "--output_dir",
        str(work_dir),
        "--output_format",
        "all",
    ]
    language = _language_code(settings.whisper_language)
    if language:
        command.extend(["--language", language])
    if settings.whisper_initial_prompt:
        command.extend(["--initial_prompt", settings.whisper_initial_prompt])
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_transcribe_timeout(settings, audio_path),
    )
    if result.returncode != 0:
        raise RuntimeError(f"whisper failed: {result.stderr.strip() or result.stdout.strip()}")

    base = work_dir / audio_path.stem
    _copy_text_if_exists(base.with_suffix(".txt"), txt_path, settings)
    _copy_text_if_exists(base.with_suffix(".srt"), srt_path, settings)
    _copy_json_if_exists(base.with_suffix(".json"), json_path, settings)
    if not valid_json_file(json_path):
        write_json_atomic(json_path, {"segments": []})
    if not txt_path.exists():
        write_text_atomic(txt_path, "")
    if not srt_path.exists():
        write_text_atomic(srt_path, "")


def _run_funasr_with_whisper_fallback(
    settings: Settings,
    audio_path: Path,
    job_dir: Path,
    txt_path: Path,
    srt_path: Path,
    json_path: Path,
    *,
    control_callback: Callable[[], str | None] | None = None,
) -> None:
    def run_primary() -> None:
        if os.environ.get("VIDEO_AUTOMATION_TRANSCRIBE_CHILD") == "1":
            transcribe_audio_funasr(settings, audio_path, txt_path, srt_path, json_path)
        else:
            _run_funasr_subprocess(settings, audio_path, job_dir, control_callback=control_callback)

    def run_fallback() -> None:
        if os.environ.get("VIDEO_AUTOMATION_TRANSCRIBE_CHILD") == "1":
            transcribe_audio_faster_whisper(settings, audio_path, txt_path, srt_path, json_path)
        else:
            _run_faster_whisper_subprocess(settings, audio_path, job_dir, control_callback=control_callback)

    _run_primary_with_fallback(
        settings,
        job_dir,
        json_path,
        primary_backend="funasr",
        primary_model=str(getattr(settings, "funasr_model", "")),
        primary=run_primary,
        fallback_backend="faster-whisper",
        fallback_model=str(getattr(settings, "whisper_model", "")),
        fallback=run_fallback,
    )


def _run_primary_with_fallback(
    settings: Settings,
    job_dir: Path,
    json_path: Path,
    *,
    primary_backend: str,
    primary_model: str,
    primary: Callable[[], None],
    fallback_backend: str,
    fallback_model: str,
    fallback: Callable[[], None],
) -> None:
    primary_error = ""
    circuit_remaining = _backend_circuit_remaining(primary_backend)
    if circuit_remaining > 0:
        primary_error = f"{primary_backend} circuit breaker is open for another {circuit_remaining:.0f}s"
        _record_transcription_attempt(
            job_dir,
            backend=primary_backend,
            model=primary_model,
            status="skipped",
            duration_seconds=0.0,
            error=primary_error,
        )
    else:
        try:
            _execute_transcription_attempt(job_dir, primary_backend, primary_model, primary)
            _reset_backend_circuit(primary_backend)
            return
        except QueueControlRequested:
            raise
        except Exception as exc:
            primary_error = str(exc)
            if not isinstance(exc, TranscriptionTaskError):
                _trip_backend_circuit(settings, primary_backend)
            _remove_partial_transcripts(job_dir)

    try:
        _execute_transcription_attempt(job_dir, fallback_backend, fallback_model, fallback)
        _annotate_fallback(json_path, primary_backend, primary_error)
        return
    except QueueControlRequested:
        raise
    except Exception as exc:
        fallback_error = str(exc)
        raise RuntimeError(
            f"{primary_backend} failed and {fallback_backend} fallback also failed. "
            f"{primary_backend}: {primary_error or 'unknown error'} | "
            f"{fallback_backend}: {fallback_error or 'unknown error'}"
        ) from exc


def _execute_transcription_attempt(
    job_dir: Path,
    backend: str,
    model: str,
    operation: Callable[[], None],
) -> None:
    started = time.monotonic()
    try:
        operation()
    except QueueControlRequested as exc:
        _record_transcription_attempt(
            job_dir,
            backend=backend,
            model=model,
            status=str(exc) or "canceled",
            duration_seconds=time.monotonic() - started,
        )
        raise
    except Exception as exc:
        _record_transcription_attempt(
            job_dir,
            backend=backend,
            model=model,
            status="failed",
            duration_seconds=time.monotonic() - started,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        raise
    _record_transcription_attempt(
        job_dir,
        backend=backend,
        model=model,
        status="complete",
        duration_seconds=time.monotonic() - started,
    )


def _record_transcription_attempt(
    job_dir: Path,
    *,
    backend: str,
    model: str,
    status: str,
    duration_seconds: float,
    error: str = "",
    error_type: str = "",
) -> None:
    path = job_dir / "transcription_attempts.json"
    existing = read_json_file(path)
    attempts = list(existing.get("attempts") or []) if isinstance(existing, dict) else []
    attempt = {
        "backend": backend,
        "model": model,
        "status": status,
        "duration_seconds": round(max(0.0, duration_seconds), 3),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }
    if error:
        attempt["error"] = error[-1600:]
    if error_type:
        attempt["error_type"] = error_type
    attempts.append(attempt)
    write_json_atomic(path, {"schema_version": 1, "attempts": attempts[-20:]})


def _backend_circuit_remaining(backend: str) -> float:
    now = time.monotonic()
    with _BACKEND_CIRCUIT_LOCK:
        unhealthy_until = _BACKEND_UNHEALTHY_UNTIL.get(backend, 0.0)
        if unhealthy_until <= now:
            _BACKEND_UNHEALTHY_UNTIL.pop(backend, None)
            return 0.0
        return unhealthy_until - now


def _trip_backend_circuit(settings: Settings, backend: str) -> None:
    cooldown = max(0, int(getattr(settings, "transcribe_backend_cooldown_seconds", 1800)))
    if cooldown <= 0:
        return
    with _BACKEND_CIRCUIT_LOCK:
        _BACKEND_UNHEALTHY_UNTIL[backend] = time.monotonic() + cooldown


def _reset_backend_circuit(backend: str) -> None:
    with _BACKEND_CIRCUIT_LOCK:
        _BACKEND_UNHEALTHY_UNTIL.pop(backend, None)


def _annotate_fallback(json_path: Path, primary_backend: str, primary_error: str) -> None:
    payload = read_json_file(json_path)
    if not isinstance(payload, dict):
        return
    payload["fallback_from"] = primary_backend
    payload["fallback_reason"] = primary_error[-800:]
    payload["backend"] = f"{payload.get('backend') or 'faster-whisper'} (fallback)"
    write_json_atomic(json_path, payload)


def transcribe_audio_faster_whisper(
    settings: Settings,
    audio_path: Path,
    txt_path: Path,
    srt_path: Path,
    json_path: Path,
    *,
    progress_callback: Callable[[str], None] | None = None,
) -> None:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("WHISPER_BACKEND=faster-whisper requires installing faster-whisper") from exc

    _report_transcription_progress(progress_callback, "checking_runtime")
    _ensure_faster_whisper_cuda_ready(settings)
    _report_transcription_progress(progress_callback, "loading_model")
    model = WhisperModel(
        settings.whisper_model,
        device=settings.faster_whisper_device,
        compute_type=settings.faster_whisper_compute_type,
    )
    _report_transcription_progress(progress_callback, "model_ready")
    transcribe_options = {
        "language": _language_code(settings.whisper_language),
        "initial_prompt": settings.whisper_initial_prompt or None,
        "word_timestamps": settings.whisper_word_timestamps,
        "vad_filter": settings.whisper_vad_filter,
    }
    if settings.faster_whisper_batch_size > 1:
        try:
            from faster_whisper import BatchedInferencePipeline
        except ImportError as exc:
            raise RuntimeError("FASTER_WHISPER_BATCH_SIZE>1 requires a faster-whisper version with BatchedInferencePipeline") from exc
        batched_model = BatchedInferencePipeline(model=model)
        segments_iter, info = batched_model.transcribe(
            str(audio_path),
            batch_size=settings.faster_whisper_batch_size,
            **transcribe_options,
        )
    else:
        segments_iter, info = model.transcribe(str(audio_path), **transcribe_options)
    _report_transcription_progress(progress_callback, "transcribing")
    segments = []
    text_parts = []
    for index, segment in enumerate(segments_iter, start=1):
        _report_transcription_progress(progress_callback, "transcribing")
        text = _postprocess_text(segment.text.strip(), settings)
        text_parts.append(text)
        payload = {
            "id": index - 1,
            "start": round(float(segment.start), 3),
            "end": round(float(segment.end), 3),
            "text": text,
        }
        words = _segment_words(segment, settings)
        if words:
            payload["words"] = words
        speaker = getattr(segment, "speaker", None)
        if speaker is not None:
            payload["speaker"] = speaker
        segments.append(payload)

    _report_transcription_progress(progress_callback, "writing_outputs")
    write_text_atomic(txt_path, "\n".join(text_parts))
    write_text_atomic(srt_path, _segments_to_srt(segments))
    write_json_atomic(json_path, {
        "text": "\n".join(text_parts),
        "segments": segments,
        "language": getattr(info, "language", None),
        "duration": getattr(info, "duration", None),
        "backend": "faster-whisper",
        "model": settings.whisper_model,
        "device": settings.faster_whisper_device,
        "compute_type": settings.faster_whisper_compute_type,
        "batch_size": settings.faster_whisper_batch_size,
        "word_timestamps": settings.whisper_word_timestamps,
        "vad_filter": settings.whisper_vad_filter,
    })


def _report_transcription_progress(callback: Callable[[str], None] | None, phase: str) -> None:
    if callback is not None:
        callback(phase)


def transcribe_audio_funasr(settings: Settings, audio_path: Path, txt_path: Path, srt_path: Path, json_path: Path) -> None:
    model = create_funasr_model(settings)
    transcribe_audio_funasr_with_model(settings, model, audio_path, txt_path, srt_path, json_path)


def create_funasr_model(settings: Settings) -> Any:
    try:
        from funasr import AutoModel
    except ImportError as exc:
        raise RuntimeError("WHISPER_BACKEND=funasr requires installing funasr") from exc

    _ensure_funasr_cuda_ready(settings)
    model_kwargs: dict[str, Any] = {
        "model": settings.funasr_model,
        "device": settings.funasr_device,
    }
    if settings.funasr_vad_model:
        model_kwargs["vad_model"] = settings.funasr_vad_model
        if settings.funasr_max_segment_ms > 0:
            model_kwargs["vad_kwargs"] = {"max_single_segment_time": settings.funasr_max_segment_ms}
    if settings.funasr_punc_model:
        model_kwargs["punc_model"] = settings.funasr_punc_model
    model_kwargs["disable_update"] = True
    return AutoModel(**model_kwargs)


def transcribe_audio_funasr_with_model(
    settings: Settings,
    model: Any,
    audio_path: Path,
    txt_path: Path,
    srt_path: Path,
    json_path: Path,
) -> None:
    generate_kwargs: dict[str, Any] = {"input": str(audio_path), "sentence_timestamp": True}
    if settings.funasr_batch_size_s > 0:
        generate_kwargs["batch_size_s"] = settings.funasr_batch_size_s
    hotwords = settings.funasr_hotwords.strip()
    if hotwords:
        generate_kwargs["hotword"] = hotwords

    result = model.generate(**generate_kwargs)
    duration = _wav_duration_seconds(audio_path)
    segments = _normalize_funasr_segments(result, settings, duration)
    text_parts = [str(segment.get("text", "")).strip() for segment in segments if str(segment.get("text", "")).strip()]
    text = "\n".join(text_parts)

    write_text_atomic(txt_path, text)
    write_text_atomic(srt_path, _segments_to_srt(segments))
    write_json_atomic(json_path, {
        "text": text,
        "segments": segments,
        "language": "zh",
        "duration": duration or None,
        "backend": "funasr",
        "model": settings.funasr_model,
        "vad_model": settings.funasr_vad_model,
        "punc_model": settings.funasr_punc_model,
        "device": settings.funasr_device,
        "hotwords": hotwords,
        "batch_size_s": settings.funasr_batch_size_s,
        "max_segment_ms": settings.funasr_max_segment_ms,
    })


def _normalize_funasr_segments(result: Any, settings: Settings, duration: float) -> list[dict[str, Any]]:
    entries = result if isinstance(result, list) else [result]
    segments: list[dict[str, Any]] = []
    untimed_texts: list[str] = []

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        sentence_segments = _funasr_sentence_segments(entry, settings, duration)
        if sentence_segments:
            segments.extend(sentence_segments)
            continue
        text = _postprocess_text(str(entry.get("text") or "").strip(), settings)
        if not text:
            continue
        bounds = _funasr_entry_bounds(entry, duration)
        if bounds is None:
            untimed_texts.append(text)
            continue
        start, end = bounds
        payload: dict[str, Any] = {"id": len(segments), "start": start, "end": end, "text": text}
        speaker = entry.get("speaker") if entry.get("speaker") is not None else entry.get("spk")
        if speaker is not None:
            payload["speaker"] = speaker
        segments.append(payload)

    if untimed_texts:
        start_at = segments[-1]["end"] if segments else 0.0
        remaining = max(0.001, (duration or start_at + len(untimed_texts)) - start_at)
        weights = [max(1, len(text)) for text in untimed_texts]
        total_weight = sum(weights) or len(untimed_texts)
        cursor = start_at
        for text, weight in zip(untimed_texts, weights):
            span = max(0.3, remaining * weight / total_weight)
            segments.append({"id": len(segments), "start": round(cursor, 3), "end": round(cursor + span, 3), "text": text})
            cursor += span

    return _clean_transcript_segments(segments, duration)


def _funasr_sentence_segments(entry: dict[str, Any], settings: Settings, duration: float) -> list[dict[str, Any]]:
    raw_sentences = entry.get("sentence_info") or entry.get("sentences") or []
    if not isinstance(raw_sentences, list):
        return []
    segments = []
    for item in raw_sentences:
        if not isinstance(item, dict):
            continue
        text = _postprocess_text(str(item.get("text") or item.get("sentence") or "").strip(), settings)
        if not text:
            continue
        start_raw = item.get("start") if item.get("start") is not None else item.get("begin")
        end_raw = item.get("end") if item.get("end") is not None else item.get("finish")
        start = _funasr_time_to_seconds(start_raw, duration)
        end = _funasr_time_to_seconds(end_raw, duration)
        if start is None or end is None:
            continue
        payload: dict[str, Any] = {"id": len(segments), "start": start, "end": end, "text": text}
        speaker = item.get("speaker") if item.get("speaker") is not None else item.get("spk")
        if speaker is not None:
            payload["speaker"] = speaker
        segments.append(payload)
    return segments


def _funasr_entry_bounds(entry: dict[str, Any], duration: float) -> tuple[float, float] | None:
    timestamp = entry.get("timestamp")
    if isinstance(timestamp, list) and timestamp:
        pairs = [item for item in timestamp if isinstance(item, (list, tuple)) and len(item) >= 2]
        if pairs:
            start = _funasr_time_to_seconds(pairs[0][0], duration)
            end = _funasr_time_to_seconds(pairs[-1][1], duration)
            if start is not None and end is not None:
                return start, end
    start_raw = entry.get("start") if entry.get("start") is not None else entry.get("begin")
    end_raw = entry.get("end") if entry.get("end") is not None else entry.get("finish")
    start = _funasr_time_to_seconds(start_raw, duration)
    end = _funasr_time_to_seconds(end_raw, duration)
    if start is None or end is None:
        return None
    return start, end


def _funasr_time_to_seconds(value: Any, duration: float) -> float | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    if seconds > max(120.0, duration + 5.0):
        seconds /= 1000.0
    return round(seconds, 3)


def _clean_transcript_segments(segments: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    cleaned = []
    cursor = 0.0
    for segment in sorted(segments, key=lambda item: float(item.get("start", 0))):
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = max(cursor, float(segment.get("start", 0)))
        end = float(segment.get("end", start))
        if duration > 0:
            start = min(start, duration)
            end = min(end, duration)
            if start >= duration:
                continue
        if end <= start:
            end = min(duration, start + 0.3) if duration > 0 else start + 0.3
        if end <= start:
            continue
        payload = {
            "id": len(cleaned),
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
        }
        speaker = segment.get("speaker")
        if speaker is not None:
            payload["speaker"] = speaker
        cleaned.append(payload)
        cursor = payload["end"]
    return cleaned


def _segment_words(segment: Any, settings: Settings) -> list[dict[str, Any]]:
    words = []
    for item in getattr(segment, "words", None) or []:
        word = _postprocess_text(str(getattr(item, "word", "")).strip(), settings)
        if not word:
            continue
        try:
            start = round(float(getattr(item, "start")), 3)
            end = round(float(getattr(item, "end")), 3)
        except (TypeError, ValueError):
            continue
        payload: dict[str, Any] = {"start": start, "end": end, "word": word}
        probability = getattr(item, "probability", None)
        if probability is not None:
            try:
                payload["probability"] = round(float(probability), 4)
            except (TypeError, ValueError):
                pass
        words.append(payload)
    return words


def _run_faster_whisper_subprocess(
    settings: Settings,
    audio_path: Path,
    job_dir: Path,
    *,
    control_callback: Callable[[], str | None] | None = None,
) -> None:
    python_executable = _project_python(settings)
    model_attempts = _model_attempts(settings)
    failures = []
    deadline = time.monotonic() + _backend_attempt_timeout(settings, audio_path)
    for model in model_attempts:
        model_started = time.monotonic()
        model_reference = _resolve_model_reference(settings, model)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            failures.append(f"{model}: backend attempt time budget exhausted")
            break
        command = [
            str(python_executable),
            "-m",
            "video_automation.transcribe_runner",
            "--audio",
            str(audio_path),
            "--job-dir",
            str(job_dir),
            "--backend",
            "faster-whisper",
            "--model",
            model_reference,
        ]
        heartbeat_path = job_dir / f".transcribe-child-heartbeat-{uuid.uuid4().hex}.json"
        command.extend(["--heartbeat", str(heartbeat_path)])
        if settings.whisper_language:
            command.extend(["--language", settings.whisper_language])
        env = _transcription_child_env(settings)
        try:
            result = _run_transcription_process(
                command,
                cwd=str(settings.root),
                env=env,
                timeout=remaining,
                heartbeat_path=heartbeat_path,
                no_progress_timeout=float(
                    getattr(settings, "transcribe_no_progress_timeout_seconds", 300)
                ),
                control_callback=control_callback,
            )
        except WorkerInfrastructureError as exc:
            failures.append(f"{model}: {exc}")
            _record_transcription_attempt(
                job_dir,
                backend="faster-whisper",
                model=model,
                status="failed",
                duration_seconds=time.monotonic() - model_started,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            _remove_partial_transcripts(job_dir)
            continue
        except subprocess.TimeoutExpired as exc:
            _record_transcription_attempt(
                job_dir,
                backend="faster-whisper",
                model=model,
                status="failed",
                duration_seconds=time.monotonic() - model_started,
                error=f"hard timeout after {remaining:.1f}s",
                error_type=type(exc).__name__,
            )
            raise
        finally:
            heartbeat_path.unlink(missing_ok=True)
        if _transcript_outputs_complete(job_dir):
            _record_transcription_attempt(
                job_dir,
                backend="faster-whisper",
                model=model,
                status="complete",
                duration_seconds=time.monotonic() - model_started,
            )
            return
        detail = (
            result.stderr.strip()
            or result.stdout.strip()
            or (
                "transcription process exited without complete output files"
                if result.returncode == 0
                else f"exit code {result.returncode}"
            )
        )[-1200:]
        failures.append(f"{model}: {detail}")
        _record_transcription_attempt(
            job_dir,
            backend="faster-whisper",
            model=model,
            status="failed",
            duration_seconds=time.monotonic() - model_started,
            error=detail,
            error_type="ChildProcessError",
        )
        _remove_partial_transcripts(job_dir)
    raise RuntimeError("faster-whisper subprocess failed after model fallbacks: " + " | ".join(failures))


def _run_funasr_subprocess(
    settings: Settings,
    audio_path: Path,
    job_dir: Path,
    *,
    control_callback: Callable[[], str | None] | None = None,
) -> None:
    deadline = time.monotonic() + _backend_attempt_timeout(settings, audio_path)
    if getattr(settings, "funasr_persistent_worker", True):
        _run_funasr_persistent(
            settings,
            audio_path,
            job_dir,
            timeout_seconds=max(0.01, deadline - time.monotonic()),
            control_callback=control_callback,
        )
        return
    _run_funasr_one_shot_subprocess(
        settings,
        audio_path,
        job_dir,
        timeout_seconds=max(0.01, deadline - time.monotonic()),
        control_callback=control_callback,
    )


def _run_funasr_persistent(
    settings: Settings,
    audio_path: Path,
    job_dir: Path,
    *,
    timeout_seconds: float | None = None,
    control_callback: Callable[[], str | None] | None = None,
) -> None:
    _run_funasr_worker_request(
        settings,
        request={
            "audio_path": str(audio_path),
            "job_dir": str(job_dir),
        },
        timeout_seconds=timeout_seconds if timeout_seconds is not None else _transcribe_timeout(settings, audio_path),
        control_callback=control_callback,
    )


def warm_transcription_backend(settings: Settings) -> bool:
    if settings.whisper_backend not in {"funasr", "funasr-whisper", "funasr-faster-whisper"}:
        return False
    if not getattr(settings, "funasr_persistent_worker", True):
        return False
    try:
        _run_funasr_worker_request(
            settings,
            request={"warmup": True, "job_dir": str(settings.root / "processing")},
            timeout_seconds=900,
        )
    except Exception:
        return False
    return True


def _run_funasr_worker_request(
    settings: Settings,
    *,
    request: dict[str, Any],
    timeout_seconds: float,
    control_callback: Callable[[], str | None] | None = None,
) -> None:
    python_executable = _project_python(settings)
    command = [
        str(python_executable),
        "-m",
        "video_automation.transcribe_worker_runner",
    ]
    env = _transcription_child_env(settings)
    try:
        _FUNASR_PERSISTENT_WORKER.run(
            command=command,
            signature=_funasr_worker_signature(settings),
            request=request,
            timeout_seconds=timeout_seconds,
            no_progress_timeout_seconds=float(
                getattr(settings, "transcribe_no_progress_timeout_seconds", 300)
            ),
            cwd=settings.root,
            env=env,
            log_path=Path(getattr(settings, "logs_dir", settings.root / "logs")) / "transcription_worker.log",
            log_max_bytes=int(getattr(settings, "transcribe_worker_log_max_bytes", 5 * 1024 * 1024)),
            control_callback=control_callback,
        )
    except TranscriptionTaskError:
        job_dir = request.get("job_dir")
        if job_dir and not request.get("warmup"):
            _remove_partial_transcripts(Path(str(job_dir)))
        raise


def _funasr_worker_signature(settings: Settings) -> tuple[Any, ...]:
    return (
        str(settings.root),
        settings.funasr_model,
        settings.funasr_vad_model,
        settings.funasr_punc_model,
        settings.funasr_device,
        settings.funasr_hotwords,
        settings.funasr_batch_size_s,
        settings.funasr_max_segment_ms,
        settings.whisper_language,
        settings.subtitle_replacements,
        settings.profanity_words,
        settings.subtitle_censor_replacement,
    )


def _transcription_child_env(settings: Settings) -> dict[str, str]:
    """Freeze task-scoped transcription settings for isolated child processes."""
    env = os.environ.copy()
    mappings: tuple[tuple[str, str, Callable[[Any], str]], ...] = (
        ("WHISPER_BACKEND", "whisper_backend", str),
        ("WHISPER_MODEL", "whisper_model", str),
        ("WHISPER_LANGUAGE", "whisper_language", str),
        ("WHISPER_INITIAL_PROMPT", "whisper_initial_prompt", str),
        ("WHISPER_WORD_TIMESTAMPS", "whisper_word_timestamps", lambda value: "true" if value else "false"),
        ("WHISPER_VAD_FILTER", "whisper_vad_filter", lambda value: "true" if value else "false"),
        ("FASTER_WHISPER_DEVICE", "faster_whisper_device", str),
        ("FASTER_WHISPER_COMPUTE_TYPE", "faster_whisper_compute_type", str),
        ("FASTER_WHISPER_BATCH_SIZE", "faster_whisper_batch_size", str),
        ("FUNASR_MODEL", "funasr_model", str),
        ("FUNASR_VAD_MODEL", "funasr_vad_model", str),
        ("FUNASR_PUNC_MODEL", "funasr_punc_model", str),
        ("FUNASR_DEVICE", "funasr_device", str),
        ("FUNASR_HOTWORDS", "funasr_hotwords", str),
        ("FUNASR_BATCH_SIZE_S", "funasr_batch_size_s", str),
        ("FUNASR_MAX_SEGMENT_MS", "funasr_max_segment_ms", str),
        ("SUBTITLE_CENSOR_REPLACEMENT", "subtitle_censor_replacement", str),
        ("PROFANITY_WORDS", "profanity_words", lambda value: ",".join(value)),
        (
            "SUBTITLE_REPLACEMENTS",
            "subtitle_replacements",
            lambda value: ",".join(f"{source}=>{target}" for source, target in value),
        ),
    )
    for env_name, attribute, serialize in mappings:
        if hasattr(settings, attribute):
            env[env_name] = serialize(getattr(settings, attribute))
    return env


def _write_transcription_settings_snapshot(settings: Settings, job_dir: Path) -> dict[str, Any]:
    """Persist the non-secret settings revision used by this transcription stage."""
    field_names = (
        "whisper_backend",
        "whisper_model",
        "whisper_model_fallbacks",
        "whisper_language",
        "whisper_initial_prompt",
        "whisper_timeout_min_seconds",
        "whisper_timeout_multiplier",
        "whisper_word_timestamps",
        "whisper_vad_filter",
        "faster_whisper_device",
        "faster_whisper_compute_type",
        "faster_whisper_batch_size",
        "funasr_model",
        "funasr_vad_model",
        "funasr_punc_model",
        "funasr_device",
        "funasr_hotwords",
        "funasr_batch_size_s",
        "funasr_max_segment_ms",
        "transcribe_audio_filter",
        "profanity_words",
        "subtitle_replacements",
        "subtitle_censor_replacement",
    )
    values = {
        name: _json_safe_setting(getattr(settings, name))
        for name in field_names
        if hasattr(settings, name)
    }
    canonical = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload = {
        "schema_version": 1,
        "revision": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "settings": values,
    }
    write_json_atomic(job_dir / "transcription_settings.json", payload)
    return payload


def _json_safe_setting(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe_setting(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_setting(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _run_funasr_one_shot_subprocess(
    settings: Settings,
    audio_path: Path,
    job_dir: Path,
    *,
    timeout_seconds: float | None = None,
    control_callback: Callable[[], str | None] | None = None,
) -> None:
    python_executable = _project_python(settings)
    command = [
        str(python_executable),
        "-m",
        "video_automation.transcribe_runner",
        "--audio",
        str(audio_path),
        "--job-dir",
        str(job_dir),
        "--backend",
        "funasr",
    ]
    if settings.whisper_language:
        command.extend(["--language", settings.whisper_language])
    env = _transcription_child_env(settings)
    result = _run_transcription_process(
        command,
        cwd=str(settings.root),
        env=env,
        timeout=timeout_seconds if timeout_seconds is not None else _transcribe_timeout(settings, audio_path),
        control_callback=control_callback,
    )
    if _transcript_outputs_complete(job_dir):
        return
    detail = (
        result.stderr.strip()
        or result.stdout.strip()
        or (
            "transcription process exited without complete output files"
            if result.returncode == 0
            else f"exit code {result.returncode}"
        )
    )[-1200:]
    _remove_partial_transcripts(job_dir)
    raise RuntimeError(f"funasr subprocess failed: {detail}")


def _model_attempts(settings: Settings) -> list[str]:
    attempts = [settings.whisper_model, *settings.whisper_model_fallbacks]
    unique = []
    for model in attempts:
        value = model.strip()
        if value and value not in unique:
            unique.append(value)
    return unique


def _resolve_model_reference(settings: Settings, model: str) -> str:
    """Prefer a verified project-local model without making private .env files non-portable."""
    value = model.strip()
    path = Path(value).expanduser()
    if path.is_absolute() or "/" in value or "\\" in value:
        return str(path)
    root = Path(getattr(settings, "root", Path.cwd()))
    for candidate in (
        root / "config" / "models" / f"faster-whisper-{value}",
        root / "config" / "models" / value,
    ):
        if (candidate / "config.json").is_file() and (candidate / "model.bin").is_file():
            return str(candidate)
    return value


def _segments_to_srt(segments: list[dict[str, Any]]) -> str:
    blocks = []
    for index, segment in enumerate(segments, start=1):
        blocks.append(
            f"{index}\n"
            f"{_srt_time(float(segment['start']))} --> {_srt_time(float(segment['end']))}\n"
            f"{segment['text']}\n"
        )
    return "\n".join(blocks)


def _srt_time(seconds: float) -> str:
    milliseconds = int(round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def _postprocess_text(text: str, settings: Settings) -> str:
    replaced = apply_replacements(text, settings.subtitle_replacements)
    return censor_text(replaced, settings.profanity_words, replacement=settings.subtitle_censor_replacement)


def _copy_text_if_exists(source: Path, dest: Path, settings: Settings) -> None:
    if source.exists():
        write_text_atomic(dest, _postprocess_text(source.read_text(encoding="utf-8", errors="replace"), settings))


def _copy_json_if_exists(source: Path, dest: Path, settings: Settings) -> None:
    if not source.exists():
        return
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except ValueError:
        return
    if isinstance(payload, dict):
        payload = _replace_transcript_payload(payload, settings)
        write_json_atomic(dest, censor_transcript_payload(payload, settings.profanity_words, replacement=settings.subtitle_censor_replacement))


def _replace_transcript_payload(payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    replaced = dict(payload)
    if isinstance(replaced.get("text"), str):
        replaced["text"] = apply_replacements(replaced["text"], settings.subtitle_replacements)
    segments = replaced.get("segments")
    if isinstance(segments, list):
        next_segments = []
        for segment in segments:
            if not isinstance(segment, dict):
                next_segments.append(segment)
                continue
            value = dict(segment)
            if isinstance(value.get("text"), str):
                value["text"] = apply_replacements(value["text"], settings.subtitle_replacements)
            next_segments.append(value)
        replaced["segments"] = next_segments
    return replaced
