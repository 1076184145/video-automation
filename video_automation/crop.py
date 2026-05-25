from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .config import Settings
from .io_utils import read_json_file, write_json_atomic


def generate_vertical_crop_plan(
    settings: Settings,
    job_dir: Path,
    *,
    force: bool = False,
    target_width: int = 1080,
    target_height: int = 1920,
) -> dict[str, Any]:
    output_path = job_dir / "crop_plan.json"
    if output_path.exists() and not force:
        cached = read_json_file(output_path)
        if cached is not None:
            return cached

    manifest = read_json_file(job_dir / "manifest.json") or {}
    stream = _first_video_stream(manifest)
    source_width = _int_value(stream.get("width"))
    source_height = _int_value(stream.get("height"))
    if source_width <= 0 or source_height <= 0:
        payload = {
            "status": "unavailable",
            "reason": "no video dimensions found in manifest.json",
            "target": {"width": target_width, "height": target_height},
        }
        write_json_atomic(output_path, payload)
        return payload

    content_crop = _detect_content_crop(settings, manifest, source_width, source_height)
    content_width = int(content_crop["width"])
    content_height = int(content_crop["height"])
    crop_prefix = _crop_prefix(content_crop, source_width, source_height)

    target_ratio = target_width / target_height
    source_ratio = content_width / content_height
    anchor_x = _clamp(float(settings.crop_anchor_x), 0.0, 1.0)
    anchor_y = _clamp(float(settings.crop_anchor_y), 0.0, 1.0)
    vertical_mode = _vertical_mode(settings.vertical_mode)

    if vertical_mode == "blur":
        payload = {
            "status": "ready",
            "method": "fit_full_frame_blur_background",
            "dynamic_anchor": {
                "status": "not_needed",
                "strategy": "preserve_full_source_frame_with_blur_background",
                "fallback": "fit_full_frame_pad",
                "keyframes": [{"time": 0, "x": 0, "y": 0}],
            },
            "source": {"width": source_width, "height": source_height},
            "target": {"width": target_width, "height": target_height},
            "content_crop": content_crop,
            "crop": content_crop,
            "ffmpeg_filter": (
                f"{crop_prefix}split=2[fg][bg];"
                f"[bg]scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
                f"crop={target_width}:{target_height},gblur=sigma=28,eq=brightness=-0.08:saturation=0.9[bgv];"
                f"[fg]scale={target_width}:{target_height}:force_original_aspect_ratio=decrease[fgv];"
                f"[bgv][fgv]overlay=(W-w)/2:(H-h)/2,setsar=1"
            ),
            "notes": [
                "VERTICAL_MODE=blur preserves the full source frame as the foreground.",
                "Only the duplicated blurred background is cropped to fill 9:16; foreground content is not cropped.",
                "Stable source black borders are removed before fitting the foreground.",
            ],
        }
        write_json_atomic(output_path, payload)
        return payload

    if vertical_mode == "pad":
        payload = {
            "status": "ready",
            "method": "fit_full_frame_pad",
            "dynamic_anchor": {
                "status": "not_needed",
                "strategy": "preserve_full_source_frame",
                "fallback": "letterbox_or_pillarbox",
                "keyframes": [{"time": 0, "x": 0, "y": 0}],
            },
            "source": {"width": source_width, "height": source_height},
            "target": {"width": target_width, "height": target_height},
            "content_crop": content_crop,
            "crop": content_crop,
            "ffmpeg_filter": (
                f"{crop_prefix}scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black"
            ),
            "notes": [
                "VERTICAL_MODE=pad preserves the full source frame and avoids cutting screen content.",
                "Use VERTICAL_MODE=crop only when you intentionally want full-screen vertical crop.",
                "Stable source black borders are removed before fitting the foreground.",
            ],
        }
        write_json_atomic(output_path, payload)
        return payload

    if source_ratio > target_ratio:
        crop_height = content_height
        crop_width = round(content_height * target_ratio)
        crop_x = _anchor_crop_offset(content_width, crop_width, anchor_x)
        crop_y = 0
    else:
        crop_width = content_width
        crop_height = round(content_width / target_ratio)
        crop_x = 0
        crop_y = _anchor_crop_offset(content_height, crop_height, anchor_y)

    payload = {
        "status": "ready",
        "method": "anchored_safe_crop",
        "dynamic_anchor": {
            "status": "configured_anchor",
            "strategy": "manual_subject_anchor",
            "anchor_x": anchor_x,
            "anchor_y": anchor_y,
            "fallback": "center_anchor",
            "keyframes": [{"time": 0, "x": crop_x, "y": crop_y}],
        },
        "source": {"width": source_width, "height": source_height},
        "target": {"width": target_width, "height": target_height},
        "content_crop": content_crop,
        "crop": {"x": crop_x, "y": crop_y, "width": crop_width, "height": crop_height},
        "ffmpeg_filter": f"{crop_prefix}crop={crop_width}:{crop_height}:{crop_x}:{crop_y},scale={target_width}:{target_height}",
        "notes": [
            "VERTICAL_MODE=crop fills the vertical frame by cutting source edges.",
            "Set CROP_ANCHOR_X/CROP_ANCHOR_Y in .env when the speaker is not centered.",
            "This is still a deterministic anchor crop, not automatic face tracking.",
            "Stable source black borders are removed before applying the vertical crop.",
        ],
    }
    write_json_atomic(output_path, payload)
    return payload


def _first_video_stream(manifest: dict[str, Any]) -> dict[str, Any]:
    for stream in manifest.get("streams", []):
        if isinstance(stream, dict) and stream.get("codec_type") == "video":
            return stream
    return {}


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _anchor_crop_offset(source_size: int, crop_size: int, anchor: float) -> int:
    max_offset = max(0, source_size - crop_size)
    desired_center = source_size * anchor
    return round(_clamp(desired_center - crop_size / 2, 0, max_offset))


def _detect_content_crop(
    settings: Settings,
    manifest: dict[str, Any],
    source_width: int,
    source_height: int,
) -> dict[str, int]:
    full = {"x": 0, "y": 0, "width": source_width, "height": source_height}
    source_path = _source_path(manifest)
    if not source_path:
        return full
    duration = _float_value((manifest.get("format") or {}).get("duration"))
    sample_times = _sample_times(duration)
    candidates: list[tuple[int, int, int, int]] = []
    for seconds in sample_times:
        candidate = _cropdetect_once(settings.ffmpeg_path, source_path, seconds)
        if candidate is not None:
            candidates.append(candidate)
    if not candidates:
        return full
    crop = _median_crop(candidates)
    if not _is_meaningful_content_crop(crop, source_width, source_height):
        return full
    width, height, x, y = crop
    return {"x": x, "y": y, "width": width, "height": height}


def _source_path(manifest: dict[str, Any]) -> Path | None:
    value = manifest.get("source_path") or (manifest.get("format") or {}).get("filename")
    if not value:
        return None
    path = Path(str(value))
    return path if path.exists() else None


def _sample_times(duration: float) -> list[float]:
    if duration <= 0:
        return [0.0]
    if duration < 12:
        return [max(0.0, duration * 0.25)]
    return [duration * 0.15, duration * 0.5, duration * 0.85]


def _cropdetect_once(ffmpeg_path: str, source_path: Path, seconds: float) -> tuple[int, int, int, int] | None:
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-ss",
        f"{max(0.0, seconds):.3f}",
        "-i",
        str(source_path),
        "-frames:v",
        "45",
        "-vf",
        "cropdetect=24:16:0",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
    except (OSError, subprocess.TimeoutExpired):
        return None
    matches = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", f"{result.stdout}\n{result.stderr}")
    if not matches:
        return None
    width, height, x, y = matches[-1]
    return int(width), int(height), int(x), int(y)


def _median_crop(candidates: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    values = list(zip(*candidates))
    return tuple(int(sorted(axis)[len(axis) // 2]) for axis in values)  # type: ignore[return-value]


def _is_meaningful_content_crop(crop: tuple[int, int, int, int], source_width: int, source_height: int) -> bool:
    width, height, x, y = crop
    if width <= 0 or height <= 0 or x < 0 or y < 0:
        return False
    if x + width > source_width or y + height > source_height:
        return False
    removed_x = source_width - width
    removed_y = source_height - height
    if removed_x < source_width * 0.03 and removed_y < source_height * 0.03:
        return False
    return width >= source_width * 0.5 and height >= source_height * 0.5


def _crop_prefix(content_crop: dict[str, int], source_width: int, source_height: int) -> str:
    if (
        int(content_crop["x"]) == 0
        and int(content_crop["y"]) == 0
        and int(content_crop["width"]) == source_width
        and int(content_crop["height"]) == source_height
    ):
        return ""
    return (
        f"crop={int(content_crop['width'])}:{int(content_crop['height'])}:"
        f"{int(content_crop['x'])}:{int(content_crop['y'])},"
    )


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _vertical_mode(value: str) -> str:
    mode = str(value or "").strip().lower().replace("-", "_")
    if mode in {"blur", "fit_blur", "contain_blur", "background_blur"}:
        return "blur"
    if mode in {"pad", "fit", "fit_full", "full_frame", "letterbox"}:
        return "pad"
    return "crop"
