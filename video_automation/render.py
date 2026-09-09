from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from collections.abc import Callable
from typing import Any

from .config import Settings
from .crop import generate_vertical_crop_plan
from .highlight_cut import generate_highlight_cut
from .process_tree import process_group_popen_kwargs
from .progress import ControlCallback, ProgressCallback, run_ffmpeg_with_progress
from .resources import GPU_EXECUTION_GATE, rendering_uses_gpu
from .io_utils import read_json_file, write_json_atomic, write_text_atomic
from .subtitles import generate_clipped_ass_subtitles


LOGGER = logging.getLogger(__name__)
NVENC_PROBE_TTL_SECONDS = 60.0
_NVENC_PROBE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_NVENC_PROBE_LOCK = threading.Lock()


def generate_render_preview(
    settings: Settings,
    job_dir: Path,
    source_path: Path,
    *,
    force: bool = False,
) -> dict[str, Any]:
    preview_path = job_dir / "render_preview.json"
    script_path = job_dir / "render_review.ps1"
    output_path = job_dir / "review.mp4"
    if preview_path.exists() and script_path.exists() and not force:
        cached = read_json_file(preview_path)
        if cached is not None:
            return cached

    effective_settings, fallback_reason = effective_render_settings(settings)
    cuts = _read_json(job_dir / "cuts.json")
    clips = _kept_clips(cuts)
    command = build_render_command(effective_settings, source_path, clips, output_path)
    payload = {
        "status": "ready",
        "source_path": str(source_path),
        "output_path": str(output_path),
        "clip_count": len(clips),
        "clips": clips,
        "command": command,
        "configured_encoder": settings.render_video_encoder,
        "effective_encoder": effective_settings.render_video_encoder,
        "encoder_fallback_reason": fallback_reason or "",
        "notes": [
            "This preview does not render automatically.",
            "Run render_review.ps1 or use --render-review after reviewing cuts.json.",
        ],
    }
    write_json_atomic(preview_path, payload)
    write_text_atomic(script_path, _render_powershell(command))
    return payload


def render_review_video(
    settings: Settings,
    job_dir: Path,
    source_path: Path,
    *,
    force: bool = False,
    progress_callback: ProgressCallback | None = None,
    resource_wait_callback: Callable[[], None] | None = None,
    resource_acquired_callback: Callable[[], None] | None = None,
    control_callback: ControlCallback | None = None,
    refresh_web_preview: bool = True,
) -> Path:
    preview = generate_render_preview(settings, job_dir, source_path, force=force)
    effective_settings = _settings_for_preview(settings, preview)
    output_path = Path(preview["output_path"])
    if output_path.exists() and output_path.stat().st_size > 0 and not force:
        if refresh_web_preview:
            _refresh_web_preview(settings, job_dir, source_path=output_path, force=False)
        return output_path
    duration_seconds = _clips_duration(preview.get("clips", []))
    result = _run_ffmpeg_with_resource_gate(
        effective_settings,
        [str(part) for part in preview["command"]],
        duration_seconds=duration_seconds,
        progress_callback=progress_callback,
        timeout=_render_timeout_seconds(effective_settings, duration_seconds),
        resource_wait_callback=resource_wait_callback,
        resource_acquired_callback=resource_acquired_callback,
        control_callback=control_callback,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg review render failed: {result.stderr.strip()}")
    if refresh_web_preview:
        _refresh_web_preview(settings, job_dir, source_path=output_path, force=force)
    return output_path


def render_final_video(
    settings: Settings,
    job_dir: Path,
    source_path: Path,
    *,
    force: bool = False,
    vertical: bool = False,
    burn_subtitles: bool = False,
    subtitle_filename: str | None = None,
    output_filename: str = "final.mp4",
    progress_callback: ProgressCallback | None = None,
    resource_wait_callback: Callable[[], None] | None = None,
    resource_acquired_callback: Callable[[], None] | None = None,
    control_callback: ControlCallback | None = None,
    refresh_web_preview: bool = True,
) -> Path:
    output_path = (job_dir / output_filename).resolve()
    try:
        output_path.relative_to(job_dir.resolve())
    except ValueError as exc:
        raise RuntimeError("final render output must stay inside the job directory") from exc
    if output_path.exists() and output_path.stat().st_size > 0 and not force:
        if _valid_media_output(settings, output_path):
            if refresh_web_preview:
                _refresh_web_preview(settings, job_dir, source_path=output_path, force=False)
            return output_path
    if output_path.exists():
        _remove_failed_output(output_path)

    effective_settings, fallback_reason = effective_render_settings(settings)
    cuts = _read_json(job_dir / "cuts.json")
    clips = _kept_clips(cuts)
    if vertical:
        generate_vertical_crop_plan(settings, job_dir, force=False)
    if burn_subtitles:
        generate_clipped_ass_subtitles(
            settings,
            job_dir,
            force=force or vertical,
            output_filename=subtitle_filename or "subtitles_clipped.ass",
        )
    if getattr(settings, "render_segment_parallel_enabled", False) and len(clips) >= 2:
        return _render_final_segmented(
            settings,
            effective_settings,
            job_dir,
            source_path,
            output_path,
            clips=clips,
            vertical=vertical,
            burn_subtitles=burn_subtitles,
            subtitle_filename=subtitle_filename,
            fallback_reason=fallback_reason,
            progress_callback=progress_callback,
            resource_wait_callback=resource_wait_callback,
            resource_acquired_callback=resource_acquired_callback,
            control_callback=control_callback,
            refresh_web_preview=refresh_web_preview,
        )
    command = build_final_render_command(
        effective_settings,
        source_path,
        clips,
        output_path,
        post_filters=_final_post_filters(
            job_dir,
            vertical=vertical,
            burn_subtitles=burn_subtitles,
            subtitle_filename=subtitle_filename,
        ),
    )

    preview = {
        "status": "ready",
        "source_path": str(source_path),
        "output_path": str(output_path),
        "clip_count": len(clips),
        "clips": clips,
        "encoding_passes": 1,
        "vertical": vertical,
        "burn_subtitles": burn_subtitles,
        "subtitle_filename": subtitle_filename or "",
        "platform": _primary_platform(settings),
        "bgm_path": str(settings.bgm_path) if settings.bgm_path else "",
        "mix": {
            "source_audio_volume": settings.source_audio_volume,
            "bgm_volume": settings.bgm_volume,
        },
        "command": command,
        "configured_encoder": settings.render_video_encoder,
        "effective_encoder": effective_settings.render_video_encoder,
        "encoder_fallback_reason": fallback_reason or "",
    }
    write_json_atomic(job_dir / "final_render_preview.json", preview)

    duration_seconds = _clips_duration(clips) or _duration_from_manifest(job_dir)
    result = _run_ffmpeg_with_resource_gate(
        effective_settings,
        command,
        duration_seconds=duration_seconds,
        progress_callback=progress_callback,
        timeout=_render_timeout_seconds(effective_settings, duration_seconds),
        resource_wait_callback=resource_wait_callback,
        resource_acquired_callback=resource_acquired_callback,
        control_callback=control_callback,
    )
    if result.returncode != 0:
        _remove_failed_output(output_path)
        raise RuntimeError(f"ffmpeg final render failed: {result.stderr.strip()}")
    if not _valid_media_output(settings, output_path):
        _remove_failed_output(output_path)
        raise RuntimeError("ffmpeg final render produced an invalid or incomplete media file")
    if refresh_web_preview:
        _refresh_web_preview(settings, job_dir, source_path=output_path, force=True)
    return output_path


def render_highlight_edit(
    settings: Settings, source_path: Path, clip_dir: Path, edit: dict[str, Any], *,
    progress_callback: ProgressCallback | None = None,
    resource_wait_callback: Callable[[], None] | None = None,
    resource_acquired_callback: Callable[[], None] | None = None,
    control_callback: ControlCallback | None = None,
) -> Path:
    """Render one independent EDL/ASS pair with shared hardware gating.

    Render to a temporary sibling; keep any previous completed MP4 on failure.
    NVENC is tried first in auto mode and can fall back after a runtime failure.
    """
    from .cuts import build_filter_complex_with_crossfade
    from .llm_evaluator import check_control

    output = clip_dir / "final.mp4"
    temporary = clip_dir / "rendering.mp4"
    configured = replace(settings, render_video_encoder="h264_nvenc", render_output_fps=edit["fps"])
    effective, fallback = effective_render_settings(configured)
    filters = _final_post_filters(clip_dir, vertical=True, burn_subtitles=True, subtitle_filename="subtitles.ass")
    graph = build_filter_complex_with_crossfade(edit["spans"], post_filters=filters, output_fps=edit["fps"])
    duration = edit["duration"]
    for attempt in range(2):
        check_control(control_callback)
        command = [str(settings.ffmpeg_path), "-hide_banner", "-y", "-i", str(source_path),
                   "-filter_complex", graph, "-map", "[outv]", "-map", "[outa]",
                   *_encoding_args(effective, final=True), "-movflags", "+faststart", str(temporary)]
        write_json_atomic(clip_dir / "render_plan.json", {
            "duration": duration, "effective_encoder": effective.render_video_encoder,
            "encoder_fallback_reason": fallback, "command": command,
        })
        result = _run_ffmpeg_with_resource_gate(
            effective, command, duration_seconds=duration, progress_callback=progress_callback,
            timeout=_render_timeout_seconds(effective, duration), resource_wait_callback=resource_wait_callback,
            resource_acquired_callback=resource_acquired_callback, control_callback=control_callback,
        )
        check_control(control_callback)
        if result.returncode == 0 and valid_highlight_output(effective, temporary, duration):
            temporary.replace(output)
            return output
        if effective.render_video_encoder != "h264_nvenc" or attempt:
            raise RuntimeError(f"Highlight render failed: {result.stderr.strip()[-1200:]}")
        fallback = "NVENC render failed; retried with libx264."
        effective = replace(effective, render_video_encoder="libx264")
    raise AssertionError("unreachable")


def valid_highlight_output(settings: Settings, path: Path, expected_duration: float) -> bool:
    """Require both streams, the vertical frame and the complete planned duration."""
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    try:
        result = subprocess.run([str(settings.ffprobe_path), "-v", "error", "-show_streams", "-of", "json", str(path)],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=30, **process_group_popen_kwargs())
        if result.returncode:
            return False
        streams = json.loads(result.stdout)["streams"]
        video = next(s for s in streams if s.get("codec_type") == "video")
        audio = next(s for s in streams if s.get("codec_type") == "audio")
        a, v = float(audio["duration"]), float(video["duration"])
        # Two 30fps frames allow muxer/AAC rounding, not a truncated render.
        return (video["width"] == 1080 and video["height"] == 1920
                and abs(a - expected_duration) <= 2 / 30 and abs(v - expected_duration) <= 2 / 30
                and abs(a - v) <= 2 / 30)
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError, StopIteration):
        return False


def _render_final_segmented(
    settings: Settings,
    effective_settings: Settings,
    job_dir: Path,
    source_path: Path,
    output_path: Path,
    *,
    clips: list[dict[str, Any]],
    vertical: bool,
    burn_subtitles: bool,
    subtitle_filename: str | None,
    fallback_reason: str | None,
    progress_callback: ProgressCallback | None,
    resource_wait_callback: Callable[[], None] | None,
    resource_acquired_callback: Callable[[], None] | None,
    control_callback: ControlCallback | None,
    refresh_web_preview: bool,
) -> Path:
    """Render each kept clip as its own file in parallel, then concat.

    Only the kept ranges are decoded (input seeking skips dropped spans), and
    segments encode concurrently. Subtitle burn-in and BGM mixing run once in a
    short finish pass over the merged output; when neither is needed the
    concat itself is stream-copied into the final file with zero re-encode.
    """
    segment_filters = _final_post_filters(job_dir, vertical=vertical, burn_subtitles=False)
    workers = max(1, min(8, int(getattr(effective_settings, "render_segment_workers", 2) or 2)))
    total_duration = _clips_duration(clips)
    segments_dir = job_dir / ".render_segments"
    if segments_dir.exists():
        shutil.rmtree(segments_dir)
    segments_dir.mkdir(parents=True)

    commands: list[list[str]] = []

    def run_segment(index: int, clip: dict[str, Any]) -> None:
        segment_path = segments_dir / f"segment_{index:03d}.mp4"
        command = build_segment_render_command(
            effective_settings,
            source_path,
            clip,
            segment_path,
            post_filters=segment_filters,
        )
        commands.append(command)
        duration = _clips_duration([clip])
        result = _run_ffmpeg_with_resource_gate(
            effective_settings,
            command,
            duration_seconds=duration,
            progress_callback=None,
            timeout=_render_timeout_seconds(effective_settings, duration),
            resource_wait_callback=resource_wait_callback,
            resource_acquired_callback=resource_acquired_callback,
            control_callback=control_callback,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg segment {index:03d} render failed: {result.stderr.strip()}")
        if not _valid_media_output(effective_settings, segment_path):
            raise RuntimeError(f"segment {index:03d} produced an invalid or incomplete media file")

    pool = ThreadPoolExecutor(max_workers=min(workers, len(clips)))
    futures = []
    try:
        for index, clip in enumerate(clips):
            futures.append(pool.submit(run_segment, index, clip))
        done = 0
        for future in futures:
            future.result()
            done += 1
            if progress_callback is not None:
                progress_callback(round(done / (len(clips) + 1) * 90.0, 1))
    except BaseException:
        for future in futures:
            future.cancel()
        raise
    finally:
        pool.shutdown(wait=True)

    subtitle_path = _subtitle_burn_path(job_dir, subtitle_filename) if burn_subtitles else None
    bgm_path = settings.bgm_path if settings.bgm_path and settings.bgm_path.exists() else None
    concat_command = build_concat_command(
        effective_settings,
        [segments_dir / f"segment_{index:03d}.mp4" for index in range(len(clips))],
        output_path if subtitle_path is None and bgm_path is None else segments_dir / "merged.mp4",
    )
    finish_command: list[str] | None = None
    if subtitle_path is not None or bgm_path is not None:
        finish_command = build_segment_finish_command(
            effective_settings,
            segments_dir / "merged.mp4",
            output_path,
            subtitle_path=subtitle_path,
            bgm_path=bgm_path,
            duration=total_duration,
        )

    concat_result = run_ffmpeg_with_progress(
        [str(part) for part in concat_command],
        duration_seconds=total_duration,
        control_callback=control_callback,
        timeout=_render_timeout_seconds(effective_settings, total_duration),
    )
    if concat_result.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {concat_result.stderr.strip()}")

    if finish_command is not None:
        finish_progress = (
            (lambda percent: progress_callback(90.0 + min(100.0, max(0.0, percent)) * 0.1))
            if progress_callback is not None
            else None
        )
        finish_result = _run_ffmpeg_with_resource_gate(
            effective_settings,
            finish_command,
            duration_seconds=total_duration,
            progress_callback=finish_progress,
            timeout=_render_timeout_seconds(effective_settings, total_duration),
            resource_wait_callback=resource_wait_callback,
            resource_acquired_callback=resource_acquired_callback,
            control_callback=control_callback,
        )
        if finish_result.returncode != 0:
            _remove_failed_output(output_path)
            raise RuntimeError(f"ffmpeg finish pass failed: {finish_result.stderr.strip()}")

    if not _valid_media_output(effective_settings, output_path):
        _remove_failed_output(output_path)
        raise RuntimeError("segmented final render produced an invalid or incomplete media file")

    preview = {
        "status": "ready",
        "source_path": str(source_path),
        "output_path": str(output_path),
        "clip_count": len(clips),
        "clips": clips,
        "encoding_passes": 1,
        "mode": "segmented",
        "segment_workers": workers,
        "vertical": vertical,
        "burn_subtitles": burn_subtitles,
        "subtitle_filename": subtitle_filename or "",
        "platform": _primary_platform(settings),
        "bgm_path": str(settings.bgm_path) if settings.bgm_path else "",
        "mix": {
            "source_audio_volume": settings.source_audio_volume,
            "bgm_volume": settings.bgm_volume,
        },
        "segment_commands": commands,
        "concat_command": concat_command,
        "finish_command": finish_command or [],
        "command": finish_command or concat_command,
        "configured_encoder": settings.render_video_encoder,
        "effective_encoder": effective_settings.render_video_encoder,
        "encoder_fallback_reason": fallback_reason or "",
    }
    write_json_atomic(job_dir / "final_render_preview.json", preview)
    shutil.rmtree(segments_dir, ignore_errors=True)
    if refresh_web_preview:
        _refresh_web_preview(settings, job_dir, source_path=output_path, force=True)
    return output_path


def build_segment_render_command(
    settings: Settings,
    source_path: Path,
    clip: dict[str, Any],
    output_path: Path,
    *,
    post_filters: list[str],
) -> list[str]:
    """Render one kept clip via input seeking so dropped ranges are never decoded."""
    start = float(clip["start"])
    end = float(clip["end"])
    duration = max(0.0, end - start)
    video_filters: list[str] = []
    if settings.render_output_fps > 0:
        video_filters.append(f"fps={settings.render_output_fps}")
    video_filters.extend(post_filters)
    command = [
        str(settings.ffmpeg_path),
        "-hide_banner",
        "-y",
        "-ss",
        f"{start:.6f}",
        "-i",
        str(source_path),
        "-t",
        f"{duration:.6f}",
    ]
    if video_filters:
        command.extend(["-vf", ",".join(video_filters)])
    command.extend(["-af", "aresample=async=1:first_pts=0"])
    command.extend(_encoding_args(settings, final=True))
    command.extend(["-movflags", "+faststart", str(output_path)])
    return command


def build_concat_command(settings: Settings, segment_paths: list[Path], output_path: Path) -> list[str]:
    if not segment_paths:
        raise RuntimeError("segmented render produced no segments to concat")
    list_path = output_path.parent / f".{output_path.name}.concat.txt"
    lines = [
        f"file '{str(path.resolve()).replace(chr(92), '/')}'"
        for path in segment_paths
    ]
    write_text_atomic(list_path, "\n".join(lines) + "\n")
    return [
        str(settings.ffmpeg_path),
        "-hide_banner",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def build_segment_finish_command(
    settings: Settings,
    merged_path: Path,
    output_path: Path,
    *,
    subtitle_path: Path | None,
    bgm_path: Path | None,
    duration: float,
) -> list[str]:
    """Single short pass over the merged output: burn subtitles and/or mix BGM."""
    command: list[str] = [
        str(settings.ffmpeg_path),
        "-hide_banner",
        "-y",
        "-i",
        str(merged_path),
    ]
    filter_parts: list[str] = []
    if subtitle_path is not None:
        filter_parts.append(f"[0:v]subtitles='{_ffmpeg_filter_path(subtitle_path)}'[outv]")
    if bgm_path is not None:
        command.extend(["-stream_loop", "-1", "-i", str(bgm_path)])
        filter_parts.append(f"[0:a]volume={_volume_value(settings.source_audio_volume)}[voice]")
        filter_parts.append(
            f"[1:a]atrim=duration={max(0.1, duration):.3f},asetpts=PTS-STARTPTS,"
            f"volume={_volume_value(settings.bgm_volume)}[bgm]"
        )
        filter_parts.append("[voice][bgm]amix=inputs=2:duration=first:dropout_transition=2[outa]")
    command.extend(["-filter_complex", ";".join(filter_parts)])
    command.extend(["-map", "[outv]" if subtitle_path is not None else "0:v"])
    command.extend(["-map", "[outa]" if bgm_path is not None else "0:a"])
    command.extend(
        _segmented_finish_encoding_args(
            settings,
            reencode_video=subtitle_path is not None,
            remix_audio=bgm_path is not None,
        )
    )
    command.extend(["-movflags", "+faststart", str(output_path)])
    return command


def _segmented_finish_encoding_args(settings: Settings, *, reencode_video: bool, remix_audio: bool) -> list[str]:
    args = _encoding_args(settings, final=True)
    if not reencode_video:
        _replace_arg_value(args, "-c:v", "copy")
    if not remix_audio:
        _replace_arg_value(args, "-c:a", "copy")
        while "-b:a" in args:
            index = args.index("-b:a")
            del args[index:index + 2]
    return args


def _subtitle_burn_path(job_dir: Path, subtitle_filename: str | None) -> Path:
    if subtitle_filename:
        subtitles_path = (job_dir / subtitle_filename).resolve()
        try:
            subtitles_path.relative_to(job_dir.resolve())
        except ValueError as exc:
            raise RuntimeError("subtitle file must stay inside the job directory") from exc
    else:
        subtitles_path = job_dir / "subtitles_clipped.ass"
        if not subtitles_path.exists():
            subtitles_path = job_dir / "subtitles.ass"
    if not subtitles_path.exists():
        raise RuntimeError("subtitles.ass is missing; run subtitle styling before final render")
    return subtitles_path


def generate_highlight_render_preview(
    settings: Settings,
    job_dir: Path,
    source_path: Path,
    *,
    force: bool = False,
    output_filename: str = "highlight.mp4",
) -> dict[str, Any]:
    preview_path = job_dir / "highlight_render_preview.json"
    output_path = (job_dir / output_filename).resolve()
    try:
        output_path.relative_to(job_dir.resolve())
    except ValueError as exc:
        raise RuntimeError("highlight render output must stay inside the job directory") from exc
    if preview_path.exists() and not force:
        cached = read_json_file(preview_path)
        if cached is not None:
            return cached
    highlight_cut = read_json_file(job_dir / "highlight_cut.json")
    if not isinstance(highlight_cut, dict):
        highlight_cut = generate_highlight_cut(job_dir, force=False)
    clips = _kept_clips({"clips": highlight_cut.get("clips", [])})
    if not clips:
        raise RuntimeError("highlight_cut.json has no clips to render")
    effective_settings, fallback_reason = effective_render_settings(settings)
    command = build_final_render_command(effective_settings, source_path, clips, output_path, post_filters=[])
    preview = {
        "status": "ready",
        "source_path": str(source_path),
        "output_path": str(output_path),
        "clip_count": len(clips),
        "duration_seconds": _clips_duration(clips),
        "clips": clips,
        "command": command,
        "configured_encoder": settings.render_video_encoder,
        "effective_encoder": effective_settings.render_video_encoder,
        "encoder_fallback_reason": fallback_reason or "",
    }
    write_json_atomic(preview_path, preview)
    return preview


def render_highlight_video(
    settings: Settings,
    job_dir: Path,
    source_path: Path,
    *,
    force: bool = False,
    progress_callback: ProgressCallback | None = None,
    resource_wait_callback: Callable[[], None] | None = None,
    resource_acquired_callback: Callable[[], None] | None = None,
    control_callback: ControlCallback | None = None,
) -> Path:
    preview = generate_highlight_render_preview(settings, job_dir, source_path, force=force)
    effective_settings = _settings_for_preview(settings, preview)
    output_path = Path(preview["output_path"])
    if output_path.exists() and output_path.stat().st_size > 0 and not force:
        _refresh_web_preview(settings, job_dir, source_path=output_path, force=False)
        return output_path
    duration_seconds = _clips_duration(preview.get("clips", []))
    result = _run_ffmpeg_with_resource_gate(
        effective_settings,
        [str(part) for part in preview["command"]],
        duration_seconds=duration_seconds,
        progress_callback=progress_callback,
        timeout=_render_timeout_seconds(effective_settings, duration_seconds),
        resource_wait_callback=resource_wait_callback,
        resource_acquired_callback=resource_acquired_callback,
        control_callback=control_callback,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg highlight render failed: {result.stderr.strip()}")
    preview["status"] = "done"
    write_json_atomic(job_dir / "highlight_render_preview.json", preview)
    _refresh_web_preview(settings, job_dir, source_path=output_path, force=True)
    return output_path


def render_platform_variants(
    settings: Settings,
    job_dir: Path,
    source_path: Path,
    *,
    primary_vertical: bool,
    burn_subtitles: bool = False,
    force: bool = False,
    progress_callback: ProgressCallback | None = None,
    resource_wait_callback: Callable[[], None] | None = None,
    resource_acquired_callback: Callable[[], None] | None = None,
    control_callback: ControlCallback | None = None,
) -> dict[str, Any]:
    """Render per-platform export variants (e.g. 9:16 douyin + 16:9 bilibili).

    The primary final.mp4 already covers the first export platform's aspect;
    each additional platform with a different aspect gets its own render with
    a matching crop geometry, subtitle preset and encoder settings.
    """
    from .plans import PLATFORM_PRESETS

    targets = platform_variant_targets(settings, primary_vertical=primary_vertical)
    variants_dir = job_dir / "variants"
    variants_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = variants_dir / "platform_variants.json"
    if not targets:
        payload = {
            "status": "skipped",
            "primary_vertical": primary_vertical,
            "variants": {},
            "notes": ["All export platforms share the primary render's aspect ratio."],
        }
        write_json_atomic(manifest_path, payload)
        return payload

    variants: dict[str, Any] = {}
    for platform, vertical in targets:
        ass_preset = ASS_PRESET_BY_PLATFORM.get(platform, settings.ass_preset)
        platform_settings = replace(
            settings,
            export_platforms=(platform,),
            ass_preset=ass_preset,
        )
        subtitle_filename = f"subtitles_clipped_{platform}.ass"
        output_path = render_final_video(
            platform_settings,
            job_dir,
            source_path,
            force=force,
            vertical=vertical,
            burn_subtitles=burn_subtitles,
            subtitle_filename=subtitle_filename,
            output_filename=f"variants/{platform}.mp4",
            progress_callback=progress_callback,
            resource_wait_callback=resource_wait_callback,
            resource_acquired_callback=resource_acquired_callback,
            control_callback=control_callback,
            refresh_web_preview=False,
        )
        preset = PLATFORM_PRESETS.get(platform, {})
        variants[platform] = {
            "file": str(output_path.relative_to(job_dir)),
            "vertical": vertical,
            "resolution": preset.get("resolution", "source"),
            "ass_preset": ass_preset,
        }
    payload = {
        "status": "ready",
        "primary_vertical": primary_vertical,
        "variants": variants,
        "notes": ["Variants re-render from the source with per-platform geometry and subtitles."],
    }
    write_json_atomic(manifest_path, payload)
    return payload


ASS_PRESET_BY_PLATFORM = {
    "douyin": "douyin",
    "bilibili": "bilibili",
    "youtube_shorts": "douyin",
}


def platform_variant_targets(
    settings: Settings, *, primary_vertical: bool
) -> list[tuple[str, bool]]:
    """Export platforms needing their own render, as (platform, vertical) pairs."""
    from .plans import PLATFORM_PRESETS

    primary = _primary_platform(settings)
    targets: list[tuple[str, bool]] = []
    seen: set[str] = set()
    for raw in getattr(settings, "export_platforms", ()):
        platform = str(raw).strip().lower()
        if not platform or platform in seen:
            continue
        seen.add(platform)
        preset = PLATFORM_PRESETS.get(platform)
        if not preset:
            continue
        width_text, _, height_text = str(preset.get("resolution", "")).partition("x")
        try:
            vertical = int(height_text) > int(width_text)
        except ValueError:
            continue
        if platform == primary and vertical == primary_vertical:
            continue
        targets.append((platform, vertical))
    return targets


def render_web_preview(
    settings: Settings,
    job_dir: Path,
    *,
    source_path: Path | None = None,
    force: bool = False,
    progress_callback: ProgressCallback | None = None,
    resource_wait_callback: Callable[[], None] | None = None,
    resource_acquired_callback: Callable[[], None] | None = None,
    control_callback: ControlCallback | None = None,
) -> Path | None:
    if not settings.web_preview_enabled:
        return None
    source_path = _valid_web_preview_source(source_path) or _web_preview_source(job_dir)
    if source_path is None:
        return None
    output_path = job_dir / "web_preview.mp4"
    if (
        output_path.exists()
        and output_path.stat().st_size > 0
        and output_path.stat().st_mtime >= source_path.stat().st_mtime
        and not force
    ):
        return output_path

    effective_settings, fallback_reason = effective_render_settings(settings)
    command = build_web_preview_command(effective_settings, source_path, output_path)
    payload = {
        "status": "ready",
        "source_path": str(source_path),
        "output_path": str(output_path),
        "max_width": settings.web_preview_max_width,
        "max_height": settings.web_preview_max_height,
        "fps": settings.web_preview_fps,
        "video_bitrate": settings.web_preview_video_bitrate,
        "command": command,
        "configured_encoder": settings.render_video_encoder,
        "effective_encoder": effective_settings.render_video_encoder,
        "encoder_fallback_reason": fallback_reason or "",
    }
    write_json_atomic(job_dir / "web_preview.json", payload)
    duration_seconds = (
        _clips_duration(_kept_clips(_read_json(job_dir / "cuts.json")))
        or _duration_from_manifest(job_dir)
    )
    result = _run_ffmpeg_with_resource_gate(
        effective_settings,
        command,
        duration_seconds=duration_seconds,
        progress_callback=progress_callback,
        timeout=_render_timeout_seconds(effective_settings, duration_seconds),
        resource_wait_callback=resource_wait_callback,
        resource_acquired_callback=resource_acquired_callback,
        control_callback=control_callback,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg web preview render failed: {result.stderr.strip()}")
    payload["status"] = "done"
    write_json_atomic(job_dir / "web_preview.json", payload)
    return output_path


def _run_ffmpeg_with_resource_gate(
    settings: Settings,
    command: list[str],
    *,
    duration_seconds: float,
    progress_callback: ProgressCallback | None,
    timeout: int,
    resource_wait_callback: Callable[[], None] | None = None,
    resource_acquired_callback: Callable[[], None] | None = None,
    control_callback: ControlCallback | None = None,
):
    with GPU_EXECUTION_GATE.slot(
        enabled=rendering_uses_gpu(settings),
        on_wait=resource_wait_callback,
        on_acquired=resource_acquired_callback,
        control_callback=control_callback,
        max_wait_seconds=timeout,
        owner="ffmpeg-render",
    ):
        return run_ffmpeg_with_progress(
            command,
            duration_seconds=duration_seconds,
            progress_callback=progress_callback,
            control_callback=control_callback,
            timeout=timeout,
        )


def probe_nvenc_encoder(
    ffmpeg_path: Path | str,
    *,
    force: bool = False,
    cache_ttl_seconds: float = NVENC_PROBE_TTL_SECONDS,
) -> dict[str, Any]:
    """Open a real one-frame NVENC session instead of trusting the encoder list."""
    key = str(ffmpeg_path)
    now = time.monotonic()
    with _NVENC_PROBE_LOCK:
        cached = _NVENC_PROBE_CACHE.get(key)
        if cached and not force and now - cached[0] <= max(0.0, cache_ttl_seconds):
            return dict(cached[1])
    command = [
        key,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=640x360:r=1",
        "-frames:v",
        "1",
        "-c:v",
        "h264_nvenc",
        "-f",
        "null",
        "-",
    ]
    try:
        result = run_ffmpeg_with_progress(command, duration_seconds=1.0, timeout=15)
        available = result.returncode == 0
        detail = "" if available else result.stderr.strip()[-2000:]
    except (OSError, subprocess.TimeoutExpired) as exc:
        available = False
        detail = str(exc)
    payload = {
        "available": available,
        "detail": detail,
        "path": key,
    }
    with _NVENC_PROBE_LOCK:
        _NVENC_PROBE_CACHE[key] = (now, payload)
    return dict(payload)


def effective_render_settings(settings: Settings) -> tuple[Settings, str | None]:
    if not rendering_uses_gpu(settings):
        return settings, None
    probe = probe_nvenc_encoder(settings.ffmpeg_path)
    if probe["available"]:
        return settings, None
    detail = str(probe.get("detail") or "NVENC session could not be opened")
    reason = detail.splitlines()[0][:300]
    LOGGER.warning("NVENC unavailable; falling back to libx264: %s", reason)
    return replace(settings, render_video_encoder="libx264"), reason


def _settings_for_preview(settings: Settings, preview: dict[str, Any]) -> Settings:
    encoder = str(preview.get("effective_encoder") or "").strip()
    if encoder and encoder != settings.render_video_encoder:
        return replace(settings, render_video_encoder=encoder)
    return settings


def _valid_media_output(settings: Settings, path: Path) -> bool:
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    try:
        result = subprocess.run(
            [
                str(settings.ffprobe_path),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            **process_group_popen_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _remove_failed_output(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        LOGGER.warning("Could not remove incomplete render output: %s", path)


def _render_timeout_seconds(settings: Settings, duration_seconds: float) -> int:
    duration = max(1.0, float(duration_seconds or 0.0))
    multiplier = 2.5 if rendering_uses_gpu(settings) else 8.0
    minimum = 1800 if rendering_uses_gpu(settings) else 3600
    return int(min(12 * 3600, max(minimum, duration * multiplier + 600)))


def build_web_preview_command(settings: Settings, source_path: Path, output_path: Path) -> list[str]:
    return [
        str(settings.ffmpeg_path),
        "-hide_banner",
        "-y",
        "-i",
        str(source_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-sn",
        "-vf",
        (
            f"scale={settings.web_preview_max_width}:{settings.web_preview_max_height}:"
            f"force_original_aspect_ratio=decrease,fps={settings.web_preview_fps}"
        ),
        *_web_preview_encoding_args(settings),
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def build_render_command(settings: Settings, source_path: Path, clips: list[dict[str, float]], output_path: Path) -> list[str]:
    if not clips:
        raise RuntimeError("cuts.json has no clips marked keep=true")
    return [
        str(settings.ffmpeg_path),
        "-hide_banner",
        "-y",
        "-i",
        str(source_path),
        "-filter_complex",
        _filter_complex(clips, output_fps=settings.render_output_fps),
        "-map",
        "[outv]",
        "-map",
        "[outa]",
        *_encoding_args(settings, final=False),
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def build_final_render_command(
    settings: Settings,
    source_path: Path,
    clips: list[dict[str, float]],
    output_path: Path,
    *,
    post_filters: list[str],
) -> list[str]:
    if not clips:
        raise RuntimeError("cuts.json has no clips marked keep=true")
    duration = _clips_duration(clips)
    command = [
        str(settings.ffmpeg_path),
        "-hide_banner",
        "-y",
        "-i",
        str(source_path),
    ]
    bgm_path = settings.bgm_path if settings.bgm_path and settings.bgm_path.exists() else None
    if bgm_path:
        command.extend(["-stream_loop", "-1", "-i", str(bgm_path)])
    command.extend([
        "-filter_complex",
        _filter_complex(
            clips,
            post_filters=post_filters,
            mix_bgm=bool(bgm_path),
            duration=duration,
            source_audio_volume=settings.source_audio_volume,
            bgm_volume=settings.bgm_volume,
            output_fps=settings.render_output_fps,
        ),
        "-map",
        "[outv]",
        "-map",
        "[outa]",
        *_encoding_args(settings, final=True),
        "-movflags",
        "+faststart",
        str(output_path),
    ])
    return command


def _refresh_web_preview(settings: Settings, job_dir: Path, *, source_path: Path | None = None, force: bool) -> None:
    if not settings.web_preview_enabled:
        return
    try:
        render_web_preview(settings, job_dir, source_path=source_path, force=force)
    except Exception as exc:
        write_json_atomic(
            job_dir / "web_preview.json",
            {
                "status": "failed",
                "error": str(exc),
                "notes": [
                    "web_preview.mp4 is only used for smoother browser playback.",
                    "review.mp4 and final.mp4 were left untouched.",
                ],
            },
        )


def _web_preview_source(job_dir: Path) -> Path | None:
    for filename in ("final.mp4", "review.mp4"):
        path = job_dir / filename
        if _valid_web_preview_source(path):
            return path
    return None


def _valid_web_preview_source(path: Path | None) -> Path | None:
    if path and path.exists() and path.stat().st_size > 0:
        return path
    return None


def _web_preview_encoding_args(settings: Settings) -> list[str]:
    bitrate = settings.web_preview_video_bitrate
    gop = str(max(24, settings.web_preview_fps * 2))
    bufsize = _double_bitrate(bitrate)
    encoder = settings.render_video_encoder.strip().lower()
    if encoder in {"h264_nvenc", "nvenc"}:
        return [
            "-c:v",
            "h264_nvenc",
            "-preset",
            settings.render_nvenc_preview_preset,
            "-tune",
            "hq",
            "-rc",
            "vbr",
            "-cq",
            "30",
            "-b:v",
            bitrate,
            "-maxrate",
            bitrate,
            "-bufsize",
            bufsize,
            "-g",
            gop,
            "-bf",
            "0",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
        ]
    return [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-maxrate",
        bitrate,
        "-bufsize",
        bufsize,
        "-g",
        gop,
        "-bf",
        "0",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
    ]


def _double_bitrate(value: str) -> str:
    raw = value.strip().lower()
    suffix = ""
    if raw.endswith(("k", "m")):
        suffix = raw[-1]
        raw = raw[:-1]
    try:
        return f"{float(raw) * 2:g}{suffix}"
    except ValueError:
        return value


def _kept_clips(cuts: dict[str, Any]) -> list[dict[str, float]]:
    clips = []
    for clip in cuts.get("clips", []):
        if not clip.get("keep", True):
            continue
        try:
            start = float(clip["start"])
            end = float(clip["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        clips.append({"start": round(start, 3), "end": round(end, 3), "duration": round(end - start, 3)})
    return clips


def _filter_complex(
    clips: list[dict[str, float]],
    *,
    post_filters: list[str] | None = None,
    mix_bgm: bool = False,
    duration: float = 0.0,
    source_audio_volume: float = 1.0,
    bgm_volume: float = 0.16,
    output_fps: int = 0,
) -> str:
    parts = []
    concat_inputs = []
    for index, clip in enumerate(clips):
        start = clip["start"]
        end = clip["end"]
        video_filters = [f"trim=start={start}:end={end}", "setpts=PTS-STARTPTS"]
        if output_fps > 0:
            video_filters.append(f"fps={output_fps}")
        parts.append(f"[0:v]{','.join(video_filters)}[v{index}]")
        parts.append(
            f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,"
            f"aresample=async=1:first_pts=0[a{index}]"
        )
        concat_inputs.append(f"[v{index}][a{index}]")
    post_filters = post_filters or []
    audio_output = "basea" if mix_bgm else "outa"
    if post_filters:
        parts.append(f"{''.join(concat_inputs)}concat=n={len(clips)}:v=1:a=1[cv][{audio_output}]")
        parts.append(f"[cv]{','.join(post_filters)}[outv]")
    else:
        parts.append(f"{''.join(concat_inputs)}concat=n={len(clips)}:v=1:a=1[outv][{audio_output}]")
    if mix_bgm:
        mix_duration = max(0.1, duration)
        parts.append(f"[basea]volume={_volume_value(source_audio_volume)}[voice]")
        parts.append(f"[1:a]atrim=duration={mix_duration:.3f},asetpts=PTS-STARTPTS,volume={_volume_value(bgm_volume)}[bgm]")
        parts.append("[voice][bgm]amix=inputs=2:duration=first:dropout_transition=2[outa]")
    return ";".join(parts)


def _render_powershell(command: list[str]) -> str:
    executable, *args = command
    lines = [
        "$ErrorActionPreference = 'Stop'",
        f"& {_ps_quote(executable)} " + " ".join(_ps_quote(arg) for arg in args),
        "",
    ]
    return "\n".join(lines)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _ffmpeg_filter_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


SAFE_CROP_FILTER_RE = re.compile(
    r"^(?:crop=\d+:\d+:\d+:\d+,)?"
    r"(?:"
    r"crop=\d+:\d+:\d+:\d+,scale=\d+:\d+"
    r"|scale=\d+:\d+:force_original_aspect_ratio=decrease,pad=\d+:\d+:\(ow-iw\)/2:\(oh-ih\)/2:black"
    r"|split=2\[fg\]\[bg\];\[bg\]scale=\d+:\d+:force_original_aspect_ratio=increase,crop=\d+:\d+,gblur=sigma=\d+(?:\.\d+)?,eq=brightness=-?\d+(?:\.\d+)?:saturation=\d+(?:\.\d+)?\[bgv\];\[fg\]scale=\d+:\d+:force_original_aspect_ratio=decrease\[fgv\];\[bgv\]\[fgv\]overlay=\(W-w\)/2:\(H-h\)/2,setsar=1"
    r")$"
)


def _safe_crop_filter(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text if SAFE_CROP_FILTER_RE.fullmatch(text) else None


def _final_post_filters(
    job_dir: Path,
    *,
    vertical: bool,
    burn_subtitles: bool,
    subtitle_filename: str | None = None,
) -> list[str]:
    filters = []
    if vertical:
        crop_plan = read_json_file(job_dir / "crop_plan.json")
        crop_filter = _safe_crop_filter(crop_plan.get("ffmpeg_filter") if isinstance(crop_plan, dict) else None)
        if crop_plan and crop_plan.get("status") == "ready" and crop_filter:
            filters.append(crop_filter)
        else:
            filters.append("scale=1080:1920:force_original_aspect_ratio=increase")
            filters.append("crop=1080:1920")
    if burn_subtitles:
        subtitles_path = _subtitle_burn_path(job_dir, subtitle_filename)
        filters.append(f"subtitles='{_ffmpeg_filter_path(subtitles_path)}'")
    return filters


def _volume_value(value: float) -> str:
    return f"{max(0.0, min(2.0, float(value))):.3f}".rstrip("0").rstrip(".")


def _primary_platform(settings: Settings) -> str:
    for platform in settings.export_platforms:
        value = platform.strip().lower()
        if value:
            return value
    return "default"


def _encoding_args(settings: Settings, *, final: bool) -> list[str]:
    encoder = settings.render_video_encoder.strip().lower()
    if encoder in {"h264_nvenc", "nvenc"}:
        return _nvenc_encoding_args(settings, final=final)
    if encoder and encoder not in {"libx264", "x264"}:
        raise RuntimeError(f"Unsupported RENDER_VIDEO_ENCODER={settings.render_video_encoder!r}")
    return _x264_encoding_args(settings, final=final)


def _x264_encoding_args(settings: Settings, *, final: bool) -> list[str]:
    platform = _primary_platform(settings)
    presets = {
        "douyin": [
            "-c:v", "libx264", "-profile:v", "high", "-level", "4.2",
            "-preset", "medium", "-crf", "21", "-maxrate", "10M", "-bufsize", "20M",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        ],
        "bilibili": [
            "-c:v", "libx264", "-profile:v", "high", "-level", "4.1",
            "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
        ],
        "youtube_shorts": [
            "-c:v", "libx264", "-profile:v", "baseline", "-level", "4.0",
            "-preset", "medium", "-crf", "22", "-maxrate", "8M", "-bufsize", "16M",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        ],
    }
    if final:
        return _apply_x264_overrides(settings, presets.get(platform, [
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        ]))
    return [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
    ]


def _apply_x264_overrides(settings: Settings, args: list[str]) -> list[str]:
    result = list(args)
    preset = str(getattr(settings, "render_x264_preset", "") or "").strip()
    if preset:
        _replace_arg_value(result, "-preset", preset)
    try:
        crf = int(getattr(settings, "render_x264_crf", 0) or 0)
    except (TypeError, ValueError):
        crf = 0
    if crf > 0:
        _replace_arg_value(result, "-crf", str(crf))
    return result


def _replace_arg_value(args: list[str], option: str, value: str) -> None:
    try:
        args[args.index(option) + 1] = value
    except (ValueError, IndexError):
        args.extend([option, value])


def _nvenc_encoding_args(settings: Settings, *, final: bool) -> list[str]:
    platform = _primary_platform(settings)
    preset = settings.render_nvenc_preset if final else settings.render_nvenc_preview_preset
    cq = settings.render_nvenc_cq if final else settings.render_nvenc_preview_cq
    audio_bitrate = "192k" if final else "160k"
    bitrate = "8M" if final else "4M"

    args = [
        "-c:v", "h264_nvenc",
        "-preset", preset,
        "-tune", "hq",
        "-rc", "vbr",
        "-cq", str(cq),
        "-b:v", bitrate,
        "-pix_fmt", "yuv420p",
    ]
    if final:
        if platform == "douyin":
            args.extend(["-profile:v", "high", "-level", "4.2", "-maxrate", "10M", "-bufsize", "20M"])
        elif platform == "youtube_shorts":
            args.extend(["-profile:v", "high", "-level", "4.2", "-maxrate", "8M", "-bufsize", "16M"])
        else:
            args.extend(["-profile:v", "high", "-level", "4.2"])
    return [*args, "-c:a", "aac", "-b:a", audio_bitrate]


def _read_json(path: Path) -> dict[str, Any]:
    return read_json_file(path) or {}


def _clips_duration(clips: list[Any]) -> float:
    duration = 0.0
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        try:
            duration += float(clip.get("duration") or (float(clip["end"]) - float(clip["start"])))
        except (KeyError, TypeError, ValueError):
            continue
    return max(0.0, duration)


def _duration_from_manifest(job_dir: Path) -> float:
    manifest = read_json_file(job_dir / "manifest.json") or {}
    try:
        return float(manifest.get("duration_seconds") or 0.0)
    except (TypeError, ValueError):
        return 0.0
