from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Settings
from .crop import generate_vertical_crop_plan
from .progress import ProgressCallback, run_ffmpeg_with_progress
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
) -> Path:
    preview = generate_render_preview(settings, job_dir, source_path, force=force)
    output_path = Path(preview["output_path"])
    if output_path.exists() and output_path.stat().st_size > 0 and not force:
        return output_path
    result = run_ffmpeg_with_progress(
        [str(part) for part in preview["command"]],
        duration_seconds=_clips_duration(preview.get("clips", [])),
        progress_callback=progress_callback,
        timeout=3600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg review render failed: {result.stderr.strip()}")
    return output_path


def render_final_video(
    settings: Settings,
    job_dir: Path,
    source_path: Path,
    *,
    force: bool = False,
    vertical: bool = False,
    burn_subtitles: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> Path:
    output_path = job_dir / "final.mp4"
    if output_path.exists() and output_path.stat().st_size > 0 and not force:
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
        post_filters=_final_post_filters(job_dir, vertical=vertical, burn_subtitles=burn_subtitles),
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
        "platform": _primary_platform(settings),
        "bgm_path": str(settings.bgm_path) if settings.bgm_path else "",
        "mix": {
            "source_audio_volume": settings.source_audio_volume,
            "bgm_volume": settings.bgm_volume,
        },
        "command": command,
    }
    write_json_atomic(job_dir / "final_render_preview.json", preview)

    result = run_ffmpeg_with_progress(
        command,
        duration_seconds=_clips_duration(clips) or _duration_from_manifest(job_dir),
        progress_callback=progress_callback,
        timeout=3600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg final render failed: {result.stderr.strip()}")
    return output_path


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
        _filter_complex(clips),
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
) -> str:
    parts = []
    concat_inputs = []
    for index, clip in enumerate(clips):
        start = clip["start"]
        end = clip["end"]
        parts.append(f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{index}]")
        parts.append(f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{index}]")
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


def _final_post_filters(job_dir: Path, *, vertical: bool, burn_subtitles: bool) -> list[str]:
    filters = []
    if vertical:
        crop_plan = read_json_file(job_dir / "crop_plan.json")
        if crop_plan and crop_plan.get("status") == "ready" and crop_plan.get("ffmpeg_filter"):
            filters.append(str(crop_plan["ffmpeg_filter"]))
        else:
            filters.append("scale=1080:1920:force_original_aspect_ratio=increase")
            filters.append("crop=1080:1920")
    if burn_subtitles:
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
        return presets.get(platform, [
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        ])
    return [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
    ]


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
