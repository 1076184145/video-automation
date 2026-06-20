from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import wave
from array import array
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .config import Settings
from .io_utils import read_json_file, write_json_atomic


MEDIA_EXTENSIONS = {".mp4", ".mkv", ".mov", ".flv", ".avi", ".m4v", ".webm", ".mp3", ".m4a", ".wav"}
SILENCE_START_RE = re.compile(r"silence_start:\s*([\d.]+)")
SILENCE_END_RE = re.compile(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)")
FREEZE_START_RE = re.compile(r"freeze_start:\s*([\d.]+)")
FREEZE_END_RE = re.compile(r"freeze_end:\s*([\d.]+)\s*\|\s*freeze_duration:\s*([\d.]+)")
SHOWINFO_PTS_RE = re.compile(r"pts_time:([\d.]+)")
FFMPEG_PROGRESS_TIME_RE = re.compile(r"out_time_(?:us|ms)=(\d+)")
FFMPEG_PROGRESS_CLOCK_RE = re.compile(r"out_time=(\d+):(\d+):([\d.]+)")
DECODE_ERROR_PATTERNS = (
    "error while decoding",
    "invalid data",
    "left block unavailable",
    "reference",
    "bytestream",
    "corrupt",
)


def run_command(args: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def quick_fingerprint(path: Path, sample_size: int = 4 * 1024 * 1024) -> dict[str, Any]:
    stat = path.stat()
    hasher, method = _fingerprint_hasher()
    hasher.update(str(stat.st_size).encode("ascii"))
    hasher.update(str(stat.st_mtime_ns).encode("ascii"))
    with path.open("rb") as handle:
        for offset in _sample_offsets(stat.st_size, sample_size):
            handle.seek(offset)
            hasher.update(handle.read(min(sample_size, stat.st_size - offset)))
    return {
        "value": hasher.hexdigest(),
        "method": method,
        "mode": "sampled_head_middle_tail",
        "sample_size_bytes": sample_size,
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _fingerprint_hasher() -> tuple[Any, str]:
    try:
        import xxhash
    except ImportError:
        return hashlib.blake2b(digest_size=16), "blake2b-128"
    return xxhash.xxh3_128(), "xxh3-128"


def _sample_offsets(size: int, sample_size: int) -> list[int]:
    if size <= sample_size * 3:
        return [0]
    return sorted({0, max(0, (size - sample_size) // 2), max(0, size - sample_size)})


def probe_media(settings: Settings, source_path: Path, manifest_path: Path, *, force: bool = False) -> dict[str, Any]:
    if manifest_path.exists() and not force:
        cached = read_json_file(manifest_path)
        if cached is not None:
            return cached
    result = run_command([
        str(settings.ffprobe_path),
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(source_path),
    ], timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")
    info = json.loads(result.stdout)
    streams = info.get("streams", [])
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    primary_video = video_streams[0] if video_streams else {}
    primary_audio = audio_streams[0] if audio_streams else {}
    fingerprint = quick_fingerprint(source_path)
    manifest = {
        "source_path": str(source_path),
        "source_name": source_path.name,
        "size_bytes": source_path.stat().st_size,
        "fingerprint": fingerprint["value"],
        "fingerprint_method": fingerprint["method"],
        "fingerprint_details": fingerprint,
        "sha256": None,
        "format": info.get("format", {}),
        "streams": streams,
        "audio_stream_count": len(audio_streams),
        "video_stream_count": len(video_streams),
        "duration_seconds": _duration_seconds(info),
        "width": _int_or_none(primary_video.get("width")),
        "height": _int_or_none(primary_video.get("height")),
        "fps": _frame_rate(primary_video),
        "codec": primary_video.get("codec_name"),
        "video_codec": primary_video.get("codec_name"),
        "audio_codec": primary_audio.get("codec_name"),
        "pixel_format": primary_video.get("pix_fmt"),
    }
    write_json_atomic(manifest_path, manifest)
    return manifest


def _duration_seconds(info: dict[str, Any]) -> float:
    raw = (info.get("format") or {}).get("duration")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _frame_rate(stream: dict[str, Any]) -> float | None:
    for key in ("avg_frame_rate", "r_frame_rate"):
        value = str(stream.get(key) or "")
        if not value or value == "0/0":
            continue
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            try:
                denominator_value = float(denominator)
                if denominator_value:
                    return round(float(numerator) / denominator_value, 3)
            except ValueError:
                continue
        try:
            return round(float(value), 3)
        except ValueError:
            continue
    return None


def extract_audio_outputs(
    settings: Settings,
    source_path: Path,
    audio_path: Path | None,
    high_quality_audio_path: Path | None,
    *,
    integrity_output_path: Path | None = None,
    duration: float = 0.0,
    force: bool = False,
) -> dict[str, Any] | None:
    needs_audio = audio_path is not None and (force or not _valid_media_output(audio_path))
    needs_high_quality = high_quality_audio_path is not None and (
        force or not _valid_media_output(high_quality_audio_path)
    )
    needs_integrity = (
        integrity_output_path is not None
        and settings.source_integrity_scan_enabled
        and (force or read_json_file(integrity_output_path) is None)
    )
    if integrity_output_path is not None and not settings.source_integrity_scan_enabled:
        integrity_payload = {"status": "skipped", "reason": "SOURCE_INTEGRITY_SCAN_ENABLED=false"}
        write_json_atomic(integrity_output_path, integrity_payload)
        if not needs_audio and not needs_high_quality:
            return integrity_payload
    if not needs_audio and not needs_high_quality and not needs_integrity:
        return read_json_file(integrity_output_path) if integrity_output_path is not None else None

    pending: list[tuple[Path, Path]] = []
    command = [
        str(settings.ffmpeg_path),
        "-hide_banner",
        "-y",
    ]
    if needs_integrity:
        command.extend([
            "-v",
            "error",
            "-nostats",
            "-progress",
            "pipe:1",
        ])
    command.extend([
        "-i",
        str(source_path),
    ])
    if needs_audio and audio_path is not None:
        temp_audio = _temp_media_path(audio_path)
        pending.append((temp_audio, audio_path))
        command.extend(["-map", "0:a:0", "-vn"])
        if settings.transcribe_audio_filter:
            command.extend(["-af", settings.transcribe_audio_filter])
        command.extend([
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(temp_audio),
        ])
    if needs_high_quality and high_quality_audio_path is not None:
        temp_high_quality = _temp_media_path(high_quality_audio_path)
        pending.append((temp_high_quality, high_quality_audio_path))
        command.extend([
            "-map",
            "0:a:0",
            "-vn",
            "-c:a",
            "flac",
            str(temp_high_quality),
        ])
    if needs_integrity:
        command.extend([
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-f",
            "null",
            os.devnull,
        ])

    _remove_paths(temp for temp, _target in pending)
    timeout = 3600
    if needs_integrity:
        timeout = max(timeout, _integrity_scan_timeout(settings, duration))
    try:
        result = run_command(command, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _remove_paths(temp for temp, _target in pending)
        integrity_payload = None
        if needs_integrity and integrity_output_path is not None:
            integrity_payload = _combined_integrity_payload(
                settings,
                duration,
                returncode=None,
                stdout=str(exc.stdout or ""),
                stderr=str(exc.stderr or ""),
                timeout_seconds=timeout,
            )
            write_json_atomic(integrity_output_path, integrity_payload)
        if pending:
            raise RuntimeError(f"ffmpeg media preparation timed out after {timeout}s") from exc
        return integrity_payload

    integrity_payload = None
    if needs_integrity and integrity_output_path is not None:
        integrity_payload = _combined_integrity_payload(
            settings,
            duration,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            timeout_seconds=timeout,
        )
        write_json_atomic(integrity_output_path, integrity_payload)
    if result.returncode != 0:
        _remove_paths(temp for temp, _target in pending)
        if pending:
            raise RuntimeError(f"ffmpeg media preparation failed: {result.stderr.strip()}")
        return integrity_payload
    missing = [temp for temp, _target in pending if not _valid_media_output(temp)]
    if missing:
        _remove_paths(temp for temp, _target in pending)
        raise RuntimeError("ffmpeg audio extraction finished without creating all requested outputs")
    for temp, target in pending:
        os.replace(temp, target)
    return integrity_payload


def extract_audio(settings: Settings, source_path: Path, audio_path: Path, *, force: bool = False) -> None:
    extract_audio_outputs(settings, source_path, audio_path, None, force=force)


def extract_high_quality_audio(settings: Settings, source_path: Path, audio_path: Path, *, force: bool = False) -> None:
    extract_audio_outputs(settings, source_path, None, audio_path, force=force)


def generate_thumbnail(settings: Settings, source_path: Path, thumbnail_path: Path, duration: float, *, force: bool = False) -> dict[str, Any]:
    payload_path = thumbnail_path.with_suffix(".json")
    if thumbnail_path.exists() and thumbnail_path.stat().st_size > 0 and payload_path.exists() and not force:
        cached = read_json_file(payload_path)
        if cached is not None:
            return cached
    timestamp = max(0.0, min(duration * 0.12, 30.0)) if duration > 0 else 0.0
    temp_path = _temp_media_path(thumbnail_path)
    result = run_command([
        str(settings.ffmpeg_path),
        "-hide_banner",
        "-y",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        str(source_path),
        "-frames:v",
        "1",
        "-vf",
        "scale=320:-1",
        "-q:v",
        "3",
        str(temp_path),
    ], timeout=120)
    if result.returncode != 0:
        payload = {"status": "unavailable", "reason": result.stderr.strip(), "timestamp": timestamp}
        write_json_atomic(payload_path, payload)
        return payload
    os.replace(temp_path, thumbnail_path)
    payload = {"status": "ready", "path": str(thumbnail_path), "timestamp": round(timestamp, 3)}
    write_json_atomic(payload_path, payload)
    return payload


def detect_decode_errors(
    settings: Settings,
    source_path: Path,
    duration: float,
    output_path: Path,
    *,
    force: bool = False,
) -> dict[str, Any]:
    if output_path.exists() and not force:
        cached = read_json_file(output_path)
        if cached is not None:
            return cached
    if not settings.source_integrity_scan_enabled:
        payload = {"status": "skipped", "reason": "SOURCE_INTEGRITY_SCAN_ENABLED=false"}
        write_json_atomic(output_path, payload)
        return payload
    timeout = _integrity_scan_timeout(settings, duration)
    command = [
        str(settings.ffmpeg_path),
        "-hide_banner",
        "-v",
        "error",
        "-nostats",
        "-progress",
        "pipe:1",
        "-xerror",
        "-err_detect",
        "explode",
        "-i",
        str(source_path),
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
        "-f",
        "null",
        "-",
    ]
    try:
        result = run_command(command, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        payload = {
            "status": "timeout",
            "scan_mode": "decode_until_first_error",
            "duration_seconds": round(duration, 3),
            "timeout_seconds": timeout,
            "first_error_at_seconds": _last_progress_seconds(str(exc.stdout or "")),
            "errors": _limit_errors(str(exc.stderr or ""), settings.source_integrity_scan_max_errors),
        }
        write_json_atomic(output_path, payload)
        return payload
    error_lines = _limit_errors(result.stderr, settings.source_integrity_scan_max_errors)
    looks_corrupt = _looks_like_decode_error(error_lines)
    payload = {
        "status": "ok" if result.returncode == 0 else "corrupt" if looks_corrupt else "failed",
        "scan_mode": "decode_until_first_error",
        "duration_seconds": round(duration, 3),
        "first_error_at_seconds": None if result.returncode == 0 else _last_progress_seconds(result.stdout),
        "error_count": len([line for line in result.stderr.splitlines() if line.strip()]),
        "errors": error_lines,
        "truncated": len(error_lines) >= settings.source_integrity_scan_max_errors,
    }
    write_json_atomic(output_path, payload)
    return payload


def _integrity_scan_timeout(settings: Settings, duration: float) -> int:
    return max(120, min(7200, int(max(duration, 1.0) * settings.source_integrity_scan_timeout_multiplier)))


def _combined_integrity_payload(
    settings: Settings,
    duration: float,
    *,
    returncode: int | None,
    stdout: str,
    stderr: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    error_lines = _limit_errors(stderr, settings.source_integrity_scan_max_errors)
    looks_corrupt = _looks_like_decode_error(error_lines)
    timed_out = returncode is None
    status = "timeout" if timed_out else "corrupt" if looks_corrupt else "ok" if returncode == 0 else "failed"
    return {
        "status": status,
        "scan_mode": "combined_full_decode",
        "duration_seconds": round(duration, 3),
        "timeout_seconds": timeout_seconds,
        "first_error_at_seconds": None,
        "scan_completed_at_seconds": _last_progress_seconds(stdout),
        "error_count": len([line for line in stderr.splitlines() if line.strip()]),
        "errors": error_lines,
        "truncated": len(error_lines) >= settings.source_integrity_scan_max_errors,
    }


def _limit_errors(stderr: str, limit: int) -> list[str]:
    return [line.strip() for line in stderr.splitlines() if line.strip()][:limit]


def _looks_like_decode_error(lines: list[str]) -> bool:
    joined = "\n".join(lines).lower()
    return any(pattern in joined for pattern in DECODE_ERROR_PATTERNS)


def _last_progress_seconds(stdout: str) -> float | None:
    latest: float | None = None
    for match in FFMPEG_PROGRESS_TIME_RE.finditer(stdout):
        latest = max(latest or 0.0, int(match.group(1)) / 1_000_000)
    for match in FFMPEG_PROGRESS_CLOCK_RE.finditer(stdout):
        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = float(match.group(3))
        latest = max(latest or 0.0, hours * 3600 + minutes * 60 + seconds)
    return round(latest, 3) if latest is not None else None


def generate_waveform(settings: Settings, audio_path: Path, waveform_path: Path, *, force: bool = False) -> dict[str, Any]:
    if waveform_path.exists() and not force:
        cached = read_json_file(waveform_path)
        if cached is not None:
            return cached
    if not _tool_exists(settings.audiowaveform_path):
        payload = _generate_waveform_fallback(audio_path, reason="audiowaveform_not_configured", tool=str(settings.audiowaveform_path))
        write_json_atomic(waveform_path, payload)
        return payload
    temp_path = waveform_path.with_suffix(".tmp.json")
    result = run_command([
        str(settings.audiowaveform_path),
        "-i",
        str(audio_path),
        "-o",
        str(temp_path),
        "-b",
        "8",
        "--pixels-per-second",
        "20",
    ], timeout=1800)
    if result.returncode != 0:
        payload = _generate_waveform_fallback(
            audio_path,
            reason="audiowaveform_failed",
            tool=str(settings.audiowaveform_path),
            error=result.stderr.strip(),
        )
        write_json_atomic(waveform_path, payload)
        return payload
    try:
        payload = json.loads(temp_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {"status": "failed", "tool": str(settings.audiowaveform_path), "error": "invalid waveform json", "data": []}
    if isinstance(payload, dict):
        payload["status"] = payload.get("status") or "ready"
        payload["tool"] = str(settings.audiowaveform_path)
    write_json_atomic(waveform_path, payload if isinstance(payload, dict) else {"status": "failed", "data": []})
    if temp_path.exists():
        temp_path.unlink()
    return read_json_file(waveform_path) or {"status": "failed", "data": []}


def _generate_waveform_fallback(audio_path: Path, *, reason: str, tool: str, error: str = "") -> dict[str, Any]:
    try:
        with wave.open(str(audio_path), "rb") as handle:
            channels = max(1, handle.getnchannels())
            sample_width = handle.getsampwidth()
            frame_rate = max(1, handle.getframerate())
            frame_count = handle.getnframes()
            pixels_per_second = 20
            frames_per_bucket = max(1, int(frame_rate / pixels_per_second))
            data: list[int] = []
            while True:
                frames = handle.readframes(frames_per_bucket)
                if not frames:
                    break
                samples = _pcm_samples(frames, sample_width, channels)
                if not samples:
                    continue
                min_value = min(samples)
                max_value = max(samples)
                scale = 128.0 / float(_sample_peak(sample_width))
                data.extend([
                    int(max(-128, min(127, round(min_value * scale)))),
                    int(max(-128, min(127, round(max_value * scale)))),
                ])
        return {
            "status": "ready",
            "source": "python_wave_fallback",
            "fallback_reason": reason,
            "tool": tool,
            "error": error,
            "sample_rate": frame_rate,
            "channels": channels,
            "bits": sample_width * 8,
            "pixels_per_second": pixels_per_second,
            "duration": round(frame_count / float(frame_rate), 3),
            "data": data,
        }
    except (OSError, wave.Error, ValueError) as exc:
        return {"status": "failed", "source": "python_wave_fallback", "fallback_reason": reason, "tool": tool, "error": error or str(exc), "data": []}


def _pcm_samples(frames: bytes, sample_width: int, channels: int) -> list[int]:
    if sample_width == 2:
        values = array("h")
        values.frombytes(frames)
        if channels <= 1:
            return list(values)
        return [sum(values[index:index + channels]) // channels for index in range(0, len(values), channels)]
    if sample_width == 1:
        values = [byte - 128 for byte in frames]
        if channels <= 1:
            return values
        return [sum(values[index:index + channels]) // channels for index in range(0, len(values), channels)]
    return []


def _sample_peak(sample_width: int) -> int:
    if sample_width == 1:
        return 128
    return 32768


def _temp_media_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.tmp{path.suffix}")


def _valid_media_output(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _remove_paths(paths: Iterable[Path]) -> None:
    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _tool_exists(path: Path) -> bool:
    return path.exists() if path.is_absolute() else shutil.which(str(path)) is not None


def detect_silence(settings: Settings, audio_path: Path, duration: float, silence_path: Path, *, force: bool = False) -> dict[str, Any]:
    if silence_path.exists() and not force:
        cached = read_json_file(silence_path)
        if cached is not None:
            return cached
    result = run_command([
        str(settings.ffmpeg_path),
        "-hide_banner",
        "-i",
        str(audio_path),
        "-af",
        f"silencedetect=n={settings.silence_threshold_db}dB:d={settings.silence_min_length_seconds}",
        "-f",
        "null",
        "-",
    ], timeout=3600)
    text = "\n".join([result.stdout, result.stderr])
    silences = parse_silence_output(text)
    payload = {
        "threshold_db": settings.silence_threshold_db,
        "min_silence_length_seconds": settings.silence_min_length_seconds,
        "min_gap_seconds": settings.silence_min_gap_seconds,
        "duration_seconds": duration,
        "silences": silences,
    }
    write_json_atomic(silence_path, payload)
    return payload


def detect_visual_events(
    settings: Settings,
    source_path: Path,
    duration: float,
    freeze_path: Path | None,
    scene_path: Path | None,
    *,
    force: bool = False,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    freeze_payload = None if freeze_path is None or force else read_json_file(freeze_path)
    scene_payload = None if scene_path is None or force else read_json_file(scene_path)
    needs_freeze = freeze_path is not None and freeze_payload is None
    needs_scene = scene_path is not None and scene_payload is None
    if not needs_freeze and not needs_scene:
        return freeze_payload, scene_payload

    command = [
        str(settings.ffmpeg_path),
        "-hide_banner",
        "-nostats",
    ]
    if settings.visual_detect_keyframes_only:
        command.extend(["-skip_frame", "nokey"])
    command.extend([
        "-i",
        str(source_path),
    ])
    freeze_filter = f"freezedetect=n={settings.freeze_noise_db}dB:d={settings.freeze_min_duration_seconds}"
    scene_filter = f"select='gt(scene,{settings.scene_threshold})',showinfo"
    if needs_freeze and needs_scene:
        prefilters = _visual_prefilters(settings)
        prefix = f"{','.join(prefilters)}," if prefilters else ""
        command.extend([
            "-filter_complex",
            (
                f"[0:v:0]{prefix}split=2[freeze_src][scene_src];"
                f"[freeze_src]{freeze_filter}[freeze_out];"
                f"[scene_src]{scene_filter},nullsink"
            ),
            "-map",
            "[freeze_out]",
            "-an",
            "-f",
            "null",
            os.devnull,
        ])
    else:
        terminal_filter = freeze_filter if needs_freeze else scene_filter
        command.extend([
            "-vf",
            _visual_filter_chain(settings, terminal_filter),
            "-an",
            "-f",
            "null",
            os.devnull,
        ])
    result = run_command(command, timeout=3600)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg visual detection failed: {result.stderr.strip()}")
    text = "\n".join([result.stdout, result.stderr])
    if needs_freeze and freeze_path is not None:
        freeze_payload = _freeze_detection_payload(settings, duration, freeze_filter, text)
        write_json_atomic(freeze_path, freeze_payload)
    if needs_scene and scene_path is not None:
        scene_payload = _scene_detection_payload(settings, duration, scene_filter, text)
        write_json_atomic(scene_path, scene_payload)
    return freeze_payload, scene_payload


def detect_freeze(settings: Settings, source_path: Path, duration: float, freeze_path: Path, *, force: bool = False) -> dict[str, Any]:
    payload, _scene_payload = detect_visual_events(
        settings,
        source_path,
        duration,
        freeze_path,
        None,
        force=force,
    )
    return payload or {}


def detect_scenes(settings: Settings, source_path: Path, duration: float, scene_path: Path, *, force: bool = False) -> dict[str, Any]:
    _freeze_payload, payload = detect_visual_events(
        settings,
        source_path,
        duration,
        None,
        scene_path,
        force=force,
    )
    return payload or {}


def _freeze_detection_payload(
    settings: Settings,
    duration: float,
    terminal_filter: str,
    text: str,
) -> dict[str, Any]:
    return {
        "noise_db": settings.freeze_noise_db,
        "min_freeze_duration_seconds": settings.freeze_min_duration_seconds,
        "keyframes_only": settings.visual_detect_keyframes_only,
        "analysis_fps": settings.visual_detect_fps,
        "analysis_width": settings.visual_detect_width,
        "video_filter": _visual_filter_chain(settings, terminal_filter),
        "duration_seconds": duration,
        "freezes": parse_freeze_output(text),
    }


def _scene_detection_payload(
    settings: Settings,
    duration: float,
    terminal_filter: str,
    text: str,
) -> dict[str, Any]:
    scenes = parse_scene_output(text)
    return {
        "threshold": settings.scene_threshold,
        "keyframes_only": settings.visual_detect_keyframes_only,
        "analysis_fps": settings.visual_detect_fps,
        "analysis_width": settings.visual_detect_width,
        "video_filter": _visual_filter_chain(settings, terminal_filter),
        "duration_seconds": duration,
        "scene_count": len(scenes),
        "scenes": scenes,
    }


def parse_silence_output(text: str) -> list[dict[str, float]]:
    starts: list[float] = []
    silences: list[dict[str, float]] = []
    for line in text.splitlines():
        start_match = SILENCE_START_RE.search(line)
        if start_match:
            starts.append(float(start_match.group(1)))
            continue
        end_match = SILENCE_END_RE.search(line)
        if end_match:
            end = float(end_match.group(1))
            duration = float(end_match.group(2))
            start = starts.pop(0) if starts else max(0.0, end - duration)
            silences.append({"start": round(start, 3), "end": round(end, 3), "duration": round(duration, 3)})
    return silences


def parse_freeze_output(text: str) -> list[dict[str, float]]:
    starts: list[float] = []
    freezes: list[dict[str, float]] = []
    for line in text.splitlines():
        start_match = FREEZE_START_RE.search(line)
        if start_match:
            starts.append(float(start_match.group(1)))
            continue
        end_match = FREEZE_END_RE.search(line)
        if end_match:
            end = float(end_match.group(1))
            duration = float(end_match.group(2))
            start = starts.pop(0) if starts else max(0.0, end - duration)
            freezes.append({"start": round(start, 3), "end": round(end, 3), "duration": round(duration, 3)})
    return freezes


def parse_scene_output(text: str) -> list[dict[str, float]]:
    scenes = []
    seen: set[float] = set()
    for line in text.splitlines():
        match = SHOWINFO_PTS_RE.search(line)
        if not match:
            continue
        time_value = round(float(match.group(1)), 3)
        if time_value in seen:
            continue
        seen.add(time_value)
        scenes.append({"time": time_value, "reason": "scene_change"})
    return scenes


def _visual_filter_chain(settings: Settings, terminal_filter: str) -> str:
    filters = _visual_prefilters(settings)
    filters.append(terminal_filter)
    return ",".join(filters)


def _visual_prefilters(settings: Settings) -> list[str]:
    filters = []
    if settings.visual_detect_fps > 0 and not settings.visual_detect_keyframes_only:
        filters.append(f"fps={settings.visual_detect_fps:g}")
    if settings.visual_detect_width > 0:
        filters.append(f"scale={settings.visual_detect_width}:-2")
    return filters
