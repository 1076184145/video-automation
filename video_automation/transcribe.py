from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any

from .config import Settings
from .io_utils import valid_json_file, write_json_atomic, write_text_atomic
from .profanity import apply_replacements, censor_text, censor_transcript_payload


def transcribe_audio(settings: Settings, audio_path: Path, job_dir: Path, *, force: bool = False) -> None:
    txt_path = job_dir / "transcript.txt"
    srt_path = job_dir / "transcript.srt"
    json_path = job_dir / "transcript.json"
    if txt_path.exists() and srt_path.exists() and valid_json_file(json_path) and not force:
        return
    if settings.whisper_backend == "faster-whisper":
        if os.environ.get("VIDEO_AUTOMATION_TRANSCRIBE_CHILD") == "1":
            transcribe_audio_faster_whisper(settings, audio_path, txt_path, srt_path, json_path)
        else:
            _run_faster_whisper_subprocess(settings, audio_path, job_dir)
        return
    if settings.whisper_backend == "funasr":
        if os.environ.get("VIDEO_AUTOMATION_TRANSCRIBE_CHILD") == "1":
            transcribe_audio_funasr(settings, audio_path, txt_path, srt_path, json_path)
        else:
            _run_funasr_subprocess(settings, audio_path, job_dir)
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
        "--language",
        settings.whisper_language,
        "--output_dir",
        str(work_dir),
        "--output_format",
        "all",
    ]
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


def transcribe_audio_faster_whisper(settings: Settings, audio_path: Path, txt_path: Path, srt_path: Path, json_path: Path) -> None:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("WHISPER_BACKEND=faster-whisper requires installing faster-whisper") from exc

    _ensure_faster_whisper_cuda_ready(settings)
    model = WhisperModel(
        settings.whisper_model,
        device=settings.faster_whisper_device,
        compute_type=settings.faster_whisper_compute_type,
    )
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
    segments = []
    text_parts = []
    for index, segment in enumerate(segments_iter, start=1):
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


def transcribe_audio_funasr(settings: Settings, audio_path: Path, txt_path: Path, srt_path: Path, json_path: Path) -> None:
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

    generate_kwargs: dict[str, Any] = {"input": str(audio_path)}
    if settings.funasr_batch_size_s > 0:
        generate_kwargs["batch_size_s"] = settings.funasr_batch_size_s
    hotwords = settings.funasr_hotwords.strip()
    if hotwords:
        generate_kwargs["hotword"] = hotwords

    model = AutoModel(**model_kwargs)
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


def _run_faster_whisper_subprocess(settings: Settings, audio_path: Path, job_dir: Path) -> None:
    python_executable = _project_python(settings)
    model_attempts = _model_attempts(settings)
    failures = []
    for model in model_attempts:
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
            model,
        ]
        if settings.whisper_language:
            command.extend(["--language", settings.whisper_language])
        env = os.environ.copy()
        env["VIDEO_AUTOMATION_TRANSCRIBE_CHILD"] = "1"
        result = subprocess.run(
            command,
            cwd=str(settings.root),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_transcribe_timeout(settings, audio_path),
        )
        if result.returncode == 0:
            return
        detail = (result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}")[-1200:]
        failures.append(f"{model}: {detail}")
        _remove_partial_transcripts(job_dir)
    raise RuntimeError("faster-whisper subprocess failed after model fallbacks: " + " | ".join(failures))


def _run_funasr_subprocess(settings: Settings, audio_path: Path, job_dir: Path) -> None:
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
    env = os.environ.copy()
    env["VIDEO_AUTOMATION_TRANSCRIBE_CHILD"] = "1"
    result = subprocess.run(
        command,
        cwd=str(settings.root),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_transcribe_timeout(settings, audio_path),
    )
    if result.returncode == 0:
        return
    detail = (result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}")[-1200:]
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


def _remove_partial_transcripts(job_dir: Path) -> None:
    for name in ("transcript.txt", "transcript.srt", "transcript.json"):
        try:
            (job_dir / name).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


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
    return max(settings.whisper_timeout_min_seconds, int(duration * settings.whisper_timeout_multiplier))


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
