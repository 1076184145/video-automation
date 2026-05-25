from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Settings
from .io_utils import read_json_file, write_text_atomic
from .profanity import apply_replacements, censor_text


def generate_ass_subtitles(settings: Settings, job_dir: Path, *, force: bool = False) -> Path:
    output_path = job_dir / "subtitles.ass"
    if output_path.exists() and output_path.stat().st_size > 0 and not force:
        return output_path

    transcript = read_json_file(job_dir / "transcript.json") or {}
    segments = _segments_from_transcript(transcript)
    segments = _prepare_subtitle_segments(settings, segments)
    write_text_atomic(output_path, _ass_document(settings, segments, _play_resolution(job_dir)))
    return output_path


def generate_clipped_ass_subtitles(settings: Settings, job_dir: Path, *, force: bool = False) -> Path:
    output_path = job_dir / "subtitles_clipped.ass"
    if output_path.exists() and output_path.stat().st_size > 0 and not force:
        return output_path

    transcript = read_json_file(job_dir / "transcript.json") or {}
    cuts = read_json_file(job_dir / "cuts.json") or {}
    segments = _segments_from_transcript(transcript)
    clips = _kept_clips(cuts)
    segments = _prepare_subtitle_segments(settings, _remap_segments_to_clips(segments, clips))
    write_text_atomic(output_path, _ass_document(settings, segments, _play_resolution(job_dir)))
    return output_path


def _segments_from_transcript(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for raw in transcript.get("segments", []):
        if not isinstance(raw, dict):
            continue
        try:
            start = float(raw["start"])
            end = float(raw["end"])
        except (KeyError, TypeError, ValueError):
            continue
        text = str(raw.get("text", "")).strip()
        if end <= start or not text:
            continue
        segments.append({"start": start, "end": end, "text": text})
    return segments


def _kept_clips(cuts: dict[str, Any]) -> list[dict[str, Any]]:
    clips = []
    for raw in cuts.get("clips", []):
        if not isinstance(raw, dict) or not raw.get("keep", True):
            continue
        try:
            start = float(raw["start"])
            end = float(raw["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end > start:
            clip: dict[str, Any] = {"start": start, "end": end, "duration": end - start}
            if raw.get("subtitle_override"):
                clip["subtitle_override"] = True
                clip["subtitle_text"] = str(raw.get("subtitle_text") or raw.get("transcript_text") or "").strip()
            clips.append(clip)
    return sorted(clips, key=lambda item: item["start"])


def _prepare_subtitle_segments(settings: Settings, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = []
    min_duration = max(0.0, float(settings.subtitle_min_duration_seconds))
    for segment in segments:
        start = float(segment["start"])
        end = float(segment["end"])
        if end - start < min_duration:
            continue
        text = apply_replacements(str(segment["text"]), settings.subtitle_replacements)
        text = censor_text(text, settings.profanity_words, replacement=settings.subtitle_censor_replacement)
        if text.strip():
            prepared.append({"start": start, "end": end, "text": text})
    return prepared


def _remap_segments_to_clips(segments: list[dict[str, Any]], clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not clips:
        return segments
    remapped = []
    output_offset = 0.0
    for clip in clips:
        clip_start = clip["start"]
        clip_end = clip["end"]
        if clip.get("subtitle_override"):
            text = str(clip.get("subtitle_text") or "").strip()
            if text:
                remapped.append({
                    "start": round(output_offset, 3),
                    "end": round(output_offset + clip["duration"], 3),
                    "text": text,
                })
            output_offset += clip["duration"]
            continue
        for segment in segments:
            overlap_start = max(float(segment["start"]), clip_start)
            overlap_end = min(float(segment["end"]), clip_end)
            if overlap_end <= overlap_start:
                continue
            remapped.append({
                "start": round(output_offset + (overlap_start - clip_start), 3),
                "end": round(output_offset + (overlap_end - clip_start), 3),
                "text": segment["text"],
            })
        output_offset += clip["duration"]
    return remapped


def _ass_document(settings: Settings, segments: list[dict[str, Any]], play_res: tuple[int, int]) -> str:
    style = _style_values(settings, play_res)
    play_res_x, play_res_y = play_res
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {play_res_x}",
        f"PlayResY: {play_res_y}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        (
            "Style: Default,"
            f"{style['font_name']},{style['font_size']},{style['primary_color']},&H000000FF,"
            f"{style['outline_color']},{style['back_color']},-1,0,0,0,100,100,0,0,1,"
            f"{style['outline']:g},{style['shadow']:g},{style['alignment']},80,80,{style['margin_v']},1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    max_lines = max(1, int(style.get("max_lines") or 2))
    for segment in segments:
        for event in _subtitle_events_for_segment(segment, style, play_res_x, max_lines):
            lines.append(
                "Dialogue: 0,"
                f"{_ass_time(event['start'])},{_ass_time(event['end'])},Default,,0,0,0,,"
                f"{_escape_ass_text(event['text'])}"
            )
    lines.append("")
    return "\n".join(lines)


def _subtitle_events_for_segment(
    segment: dict[str, Any],
    style: dict[str, Any],
    play_res_x: int,
    max_lines: int,
) -> list[dict[str, Any]]:
    text = " ".join(str(segment["text"]).split())
    if not text:
        return []
    max_chars = _max_chars_per_line(style, play_res_x)
    chunks = _split_text_chunks(text, max_chars * max_lines)
    if not chunks:
        return []

    start = float(segment["start"])
    end = float(segment["end"])
    duration = max(0.01, end - start)
    total_weight = sum(max(1, len(chunk)) for chunk in chunks)
    offset = start
    events = []
    for index, chunk in enumerate(chunks):
        if index == len(chunks) - 1:
            chunk_end = end
        else:
            chunk_end = min(end, offset + duration * (max(1, len(chunk)) / total_weight))
        wrapped = _wrap_subtitle_text(chunk, style, play_res_x, max_lines)
        if wrapped and chunk_end > offset:
            events.append({"start": round(offset, 3), "end": round(chunk_end, 3), "text": wrapped})
        offset = chunk_end
    return events


def _split_text_chunks(text: str, max_chars: int) -> list[str]:
    max_chars = max(8, max_chars)
    if len(text) <= max_chars:
        return [text]
    chunks = []
    remaining = text
    punctuation = "，。！？；、,.!?;: "
    while len(remaining) > max_chars:
        split_at = max(remaining.rfind(mark, 0, max_chars + 1) for mark in punctuation)
        if split_at < max_chars * 0.45:
            split_at = max_chars - 1
        chunk = remaining[: split_at + 1].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at + 1 :].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _style_values(settings: Settings, play_res: tuple[int, int]) -> dict[str, Any]:
    style = {
        "font_name": settings.ass_font_name,
        "font_size": settings.ass_font_size,
        "primary_color": settings.ass_primary_color,
        "outline_color": settings.ass_outline_color,
        "back_color": settings.ass_back_color,
        "alignment": settings.ass_alignment,
        "margin_v": settings.ass_margin_v,
        "outline": settings.ass_outline,
        "shadow": settings.ass_shadow,
        "max_lines": settings.ass_max_lines,
    }
    presets: dict[str, dict[str, Any]] = {
        "douyin": {
            "font_size": 52,
            "primary_color": "&H00FFFFFF",
            "outline_color": "&H00000000",
            "back_color": "&H00000000",
            "outline": 3,
            "shadow": 0,
            "alignment": 2,
            "margin_v": 180,
        },
        "bilibili": {
            "font_size": 48,
            "primary_color": "&H00FFFFFF",
            "outline_color": "&H00643B16",
            "back_color": "&H64000000",
            "outline": 3,
            "shadow": 1,
            "alignment": 2,
            "margin_v": 80,
        },
    }
    style.update(presets.get(settings.ass_preset.strip().lower(), {}))
    play_res_x, play_res_y = play_res
    if play_res_y > play_res_x and settings.ass_vertical_font_size > 0:
        style["font_size"] = settings.ass_vertical_font_size
        style["margin_v"] = max(int(style["margin_v"]), 150)
    return style


def _play_resolution(job_dir: Path) -> tuple[int, int]:
    crop_plan = read_json_file(job_dir / "crop_plan.json") or {}
    target = crop_plan.get("target") if isinstance(crop_plan.get("target"), dict) else {}
    width = _positive_int(target.get("width"))
    height = _positive_int(target.get("height"))
    if width and height:
        return width, height

    manifest = read_json_file(job_dir / "manifest.json") or {}
    for stream in manifest.get("streams", []):
        if not isinstance(stream, dict) or stream.get("codec_type") != "video":
            continue
        width = _positive_int(stream.get("width"))
        height = _positive_int(stream.get("height"))
        if width and height:
            return width, height
    return 1920, 1080


def _positive_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _wrap_subtitle_text(text: str, style: dict[str, Any], play_res_x: int) -> str:
    value = " ".join(text.split())
    if not value:
        return ""
    font_size = max(1, int(style.get("font_size") or 52))
    horizontal_margin = 160
    max_chars = max(8, min(24, int((play_res_x - horizontal_margin) / (font_size * 0.9))))
    if len(value) <= max_chars:
        return value

    lines = []
    remaining = value
    punctuation = "，。！？；：,.!?;: "
    while len(remaining) > max_chars:
        split_at = max(remaining.rfind(mark, 0, max_chars + 1) for mark in punctuation)
        if split_at < max_chars * 0.45:
            split_at = max_chars
        chunk = remaining[: split_at + 1].strip()
        if chunk:
            lines.append(chunk)
        remaining = remaining[split_at + 1 :].strip()
    if remaining:
        lines.append(remaining)
    return "\n".join(lines)


def _max_chars_per_line(style: dict[str, Any], play_res_x: int) -> int:
    font_size = max(1, int(style.get("font_size") or 52))
    horizontal_margin = 160
    return max(8, min(24, int((play_res_x - horizontal_margin) / (font_size * 0.9))))


def _wrap_subtitle_text(text: str, style: dict[str, Any], play_res_x: int, max_lines: int) -> str:
    value = " ".join(text.split())
    if not value:
        return ""
    max_chars = _max_chars_per_line(style, play_res_x)
    if len(value) <= max_chars:
        return value

    lines = []
    remaining = value
    punctuation = "，。！？；、,.!?;: "
    while len(remaining) > max_chars:
        split_at = max(remaining.rfind(mark, 0, max_chars + 1) for mark in punctuation)
        if split_at < max_chars * 0.45:
            split_at = max_chars - 1
        chunk = remaining[: split_at + 1].strip()
        if chunk:
            lines.append(chunk)
        remaining = remaining[split_at + 1 :].strip()
    if remaining:
        lines.append(remaining)
    return "\n".join(lines[:max_lines])


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, int(round(seconds * 100)))
    cs = centiseconds % 100
    total_seconds = centiseconds // 100
    sec = total_seconds % 60
    total_minutes = total_seconds // 60
    minute = total_minutes % 60
    hour = total_minutes // 60
    return f"{hour}:{minute:02d}:{sec:02d}.{cs:02d}"


def _escape_ass_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", r"\{").replace("}", r"\}").replace("\r\n", r"\N").replace("\n", r"\N")
