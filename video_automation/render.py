from __future__ import annotations

import re
from pathlib import Path
from collections.abc import Callable
from typing import Any

from .config import Settings
from .crop import generate_vertical_crop_plan
from .highlight_cut import generate_highlight_cut
from .progress import ProgressCallback, run_ffmpeg_with_progress
from .resources import GPU_EXECUTION_GATE, rendering_uses_gpu
from .io_utils import read_json_file, write_json_atomic, write_text_atomic
from .subtitles import generate_clipped_ass_subtitles


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

    cuts = _read_json(job_dir / "cuts.json")
    clips = _kept_clips(cuts)
    command = build_render_command(settings, source_path, clips, output_path)
    payload = {
        "status": "ready",
        "source_path": str(source_path),
        "output_path": str(output_path),
        "clip_count": len(clips),
        "clips": clips,
        "command": command,
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
) -> Path:
    preview = generate_render_preview(settings, job_dir, source_path, force=force)
    output_path = Path(preview["output_path"])
    if output_path.exists() and output_path.stat().st_size > 0 and not force:
        _refresh_web_preview(settings, job_dir, source_path=output_path, force=False)
        return output_path
    result = _run_ffmpeg_with_resource_gate(
        settings,
        [str(part) for part in preview["command"]],
        duration_seconds=_clips_duration(preview.get("clips", [])),
        progress_callback=progress_callback,
        timeout=3600,
        resource_wait_callback=resource_wait_callback,
        resource_acquired_callback=resource_acquired_callback,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg review render failed: {result.stderr.strip()}")
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
) -> Path:
    output_path = (job_dir / output_filename).resolve()
    try:
        output_path.relative_to(job_dir.resolve())
    except ValueError as exc:
        raise RuntimeError("final render output must stay inside the job directory") from exc
    if output_path.exists() and output_path.stat().st_size > 0 and not force:
        _refresh_web_preview(settings, job_dir, source_path=output_path, force=False)
        return output_path

    cuts = _read_json(job_dir / "cuts.json")
    clips = _kept_clips(cuts)
    if vertical:
        generate_vertical_crop_plan(settings, job_dir, force=False)
    if burn_subtitles:
        generate_clipped_ass_subtitles(settings, job_dir, force=force or vertical)
    command = build_final_render_command(
        settings,
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
    }
    write_json_atomic(job_dir / "final_render_preview.json", preview)

    result = _run_ffmpeg_with_resource_gate(
        settings,
        command,
        duration_seconds=_clips_duration(clips) or _duration_from_manifest(job_dir),
        progress_callback=progress_callback,
        timeout=3600,
        resource_wait_callback=resource_wait_callback,
        resource_acquired_callback=resource_acquired_callback,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg final render failed: {result.stderr.strip()}")
    _refresh_web_preview(settings, job_dir, source_path=output_path, force=True)
    return output_path


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
    command = build_final_render_command(settings, source_path, clips, output_path, post_filters=[])
    preview = {
        "status": "ready",
        "source_path": str(source_path),
        "output_path": str(output_path),
        "clip_count": len(clips),
        "duration_seconds": _clips_duration(clips),
        "clips": clips,
        "command": command,
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
) -> Path:
    preview = generate_highlight_render_preview(settings, job_dir, source_path, force=force)
    output_path = Path(preview["output_path"])
    if output_path.exists() and output_path.stat().st_size > 0 and not force:
        _refresh_web_preview(settings, job_dir, source_path=output_path, force=False)
        return output_path
    result = _run_ffmpeg_with_resource_gate(
        settings,
        [str(part) for part in preview["command"]],
        duration_seconds=_clips_duration(preview.get("clips", [])),
        progress_callback=progress_callback,
        timeout=3600,
        resource_wait_callback=resource_wait_callback,
        resource_acquired_callback=resource_acquired_callback,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg highlight render failed: {result.stderr.strip()}")
    preview["status"] = "done"
    write_json_atomic(job_dir / "highlight_render_preview.json", preview)
    _refresh_web_preview(settings, job_dir, source_path=output_path, force=True)
    return output_path


def render_web_preview(
    settings: Settings,
    job_dir: Path,
    *,
    source_path: Path | None = None,
    force: bool = False,
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

    command = build_web_preview_command(settings, source_path, output_path)
    payload = {
        "status": "ready",
        "source_path": str(source_path),
        "output_path": str(output_path),
        "max_width": settings.web_preview_max_width,
        "max_height": settings.web_preview_max_height,
        "fps": settings.web_preview_fps,
        "video_bitrate": settings.web_preview_video_bitrate,
        "command": command,
    }
    write_json_atomic(job_dir / "web_preview.json", payload)
    result = _run_ffmpeg_with_resource_gate(
        settings,
        command,
        duration_seconds=_duration_from_manifest(job_dir),
        progress_callback=None,
        timeout=3600,
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
):
    with GPU_EXECUTION_GATE.slot(
        enabled=rendering_uses_gpu(settings),
        on_wait=resource_wait_callback,
        on_acquired=resource_acquired_callback,
    ):
        return run_ffmpeg_with_progress(
            command,
            duration_seconds=duration_seconds,
            progress_callback=progress_callback,
            timeout=timeout,
        )


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
