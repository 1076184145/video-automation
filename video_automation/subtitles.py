from __future__ import annotations

import unicodedata
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


def segments_from_transcript(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    """Return normalized subtitle segments from a transcript payload."""
    return _segments_from_transcript(transcript)


def kept_clips(cuts: dict[str, Any]) -> list[dict[str, Any]]:
    """Return sorted kept clip ranges from a cuts payload."""
    return _kept_clips(cuts)


def prepare_subtitle_segments(settings: Settings, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply subtitle duration, replacement, and censoring rules."""
    return _prepare_subtitle_segments(settings, segments)


def remap_segments_to_clips(segments: list[dict[str, Any]], clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Move original subtitle segments onto the edited clip timeline."""
    return _remap_segments_to_clips(segments, clips)


def ass_document(settings: Settings, segments: list[dict[str, Any]], play_res: tuple[int, int]) -> str:
    """Render prepared subtitle segments as an ASS document."""
    return _ass_document(settings, segments, play_res)


def play_resolution(job_dir: Path) -> tuple[int, int]:
    """Infer subtitle play resolution from crop plan or source manifest."""
    return _play_resolution(job_dir)


def _segments_from_transcript(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for raw in transcript.get("segments", []):
        if not isinstance(raw, dict):
            continue
        word_segments = _segments_from_words(raw)
        if word_segments:
            segments.extend(word_segments)
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


def _segments_from_words(raw_segment: dict[str, Any]) -> list[dict[str, Any]]:
    raw_words = raw_segment.get("words")
    if not isinstance(raw_words, list):
        return []

    words: list[dict[str, Any]] = []
    max_word_duration = 1.8
    for raw_word in raw_words:
        if not isinstance(raw_word, dict):
            continue
        text = str(raw_word.get("word") or raw_word.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(raw_word["start"])
            end = float(raw_word["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        if end - start > max_word_duration:
            end = start + max_word_duration
        word_payload: dict[str, Any] = {"start": start, "end": end, "text": text}
        probability = raw_word.get("probability")
        if isinstance(probability, (int, float)):
            word_payload["probability"] = float(probability)
        words.append(word_payload)
    if not words:
        return []

    segments: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    current_start = 0.0
    current_end = 0.0
    max_gap = 0.55
    max_duration = 5.5
    max_width = 34

    def flush() -> None:
        if not current:
            return
        text = _join_word_texts([str(item["text"]) for item in current]).strip()
        if text:
            segment: dict[str, Any] = {
                "start": round(current_start, 3),
                "end": round(current_end, 3),
                "text": text,
                "word_count": len(current),
            }
            probabilities = [float(item["probability"]) for item in current if isinstance(item.get("probability"), (int, float))]
            if probabilities:
                segment["avg_probability"] = round(sum(probabilities) / len(probabilities), 4)
            segments.append(segment)

    for word in words:
        start = float(word["start"])
        end = float(word["end"])
        text = str(word["text"])
        if current:
            gap = start - current_end
            next_text = _join_word_texts([str(item["text"]) for item in current] + [text])
            should_split = (
                gap >= max_gap
                or end - current_start > max_duration
                or _visual_width(next_text) > max_width
                or (_ends_sentence(str(current[-1]["text"])) and _visual_width(next_text) >= 16)
            )
            if should_split:
                flush()
                current = []
        if not current:
            current_start = start
        current.append(word)
        current_end = end
    flush()
    return segments


def _join_word_texts(words: list[str]) -> str:
    result = ""
    for word in words:
        if not word:
            continue
        if result and _needs_word_space(result[-1], word[0]):
            result += " "
        result += word
    return result


def _needs_word_space(left: str, right: str) -> bool:
    return left.isascii() and right.isascii() and (left.isalnum() or left in {"'", '"'}) and (right.isalnum() or right in {"'", '"'})


def _ends_sentence(text: str) -> bool:
    return text.rstrip().endswith(("。", "！", "？", ".", "!", "?"))


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
        if text.strip() and not _looks_like_background_vocal(settings, start, end, text, segment):
            prepared_segment: dict[str, Any] = {"start": start, "end": end, "text": text}
            for key in ("avg_probability", "word_count"):
                if key in segment:
                    prepared_segment[key] = segment[key]
            prepared.append(prepared_segment)
    return prepared


def _looks_like_background_vocal(settings: Settings, start: float, end: float, text: str, segment: dict[str, Any]) -> bool:
    if not settings.subtitle_music_vocal_filter_enabled:
        return False
    duration = max(0.0, end - start)
    min_duration = max(0.0, settings.subtitle_music_vocal_min_duration_seconds)
    min_rate = max(0.0, settings.subtitle_music_vocal_min_chars_per_second)
    min_probability = max(0.0, settings.subtitle_music_vocal_min_avg_probability)
    if duration < min_duration:
        return False
    normalized = "".join(char for char in text.strip() if not char.isspace())
    if not normalized:
        return True
    compact_patterns = ["".join(pattern.split()) for pattern in settings.subtitle_music_vocal_patterns if pattern.strip()]
    if compact_patterns and any(pattern and pattern in normalized for pattern in compact_patterns):
        return True
    text_rate = len(normalized) / max(0.001, duration)
    if min_rate > 0 and text_rate < min_rate:
        return True
    probability = segment.get("avg_probability")
    if min_probability > 0 and isinstance(probability, (int, float)) and float(probability) < min_probability:
        return True
    return False


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
                **{key: segment[key] for key in ("avg_probability", "word_count") if key in segment},
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
    text = _normalize_subtitle_text(str(segment["text"]))
    if not text:
        return []
    max_lines = max(1, int(max_lines))
    max_units = _max_chars_per_line(style, play_res_x)
    # ASR backends and manual overrides can return a very long segment. Build
    # the final visual lines first, then group them into bounded events. This is
    # the last line-limit invariant before ASS serialization and deliberately
    # preserves all text instead of clipping lines that do not fit.
    visual_lines = _wrap_subtitle_lines(text, max_units)
    chunks = [
        "\n".join(visual_lines[index:index + max_lines])
        for index in range(0, len(visual_lines), max_lines)
    ]
    if not chunks:
        return []

    start = float(segment["start"])
    end = float(segment["end"])
    duration = max(0.01, end - start)
    total_weight = sum(max(1, _visual_width(chunk)) for chunk in chunks)
    offset = start
    events = []
    for index, chunk in enumerate(chunks):
        if index == len(chunks) - 1:
            chunk_end = end
        else:
            chunk_end = min(end, offset + duration * (max(1, _visual_width(chunk)) / total_weight))
        if chunk and chunk_end > offset:
            events.append({"start": round(offset, 3), "end": round(chunk_end, 3), "text": chunk})
        offset = chunk_end
    return events


def _normalize_subtitle_text(text: str) -> str:
    """Normalize ASR text and neutralize embedded ASS line-break commands."""
    return " ".join(text.replace(r"\N", " ").replace(r"\n", " ").split())


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


def _max_chars_per_line(style: dict[str, Any], play_res_x: int) -> int:
    font_size = max(1, int(style.get("font_size") or 52))
    horizontal_margin = 160
    return max(16, min(52, int((play_res_x - horizontal_margin) / (font_size * 0.45))))


def _wrap_subtitle_text(text: str, style: dict[str, Any], play_res_x: int, max_lines: int) -> str:
    value = _normalize_subtitle_text(text)
    if not value:
        return ""
    max_chars = _max_chars_per_line(style, play_res_x)
    return "\n".join(_wrap_subtitle_lines(value, max_chars)[:max(1, max_lines)])


def _wrap_subtitle_lines(text: str, max_units: int) -> list[str]:
    """Wrap normalized subtitle text without dropping overflowing lines."""
    lines: list[str] = []
    remaining = text
    while _visual_width(remaining) > max_units:
        split_at = _best_split_index(remaining, max_units)
        chunk = remaining[:split_at].strip()
        if chunk:
            lines.append(chunk)
        remaining = remaining[split_at:].strip()
    if remaining:
        lines.append(remaining)
    return lines


def _best_split_index(text: str, max_units: int) -> int:
    hard_limit = max(1, _index_for_visual_width(text, max_units))
    if hard_limit >= len(text):
        return len(text)
    min_index = max(1, _index_for_visual_width(text, max(1, int(max_units * 0.45))))
    punctuation = "，。！？；、,.!?;: "
    split_at = max(text.rfind(mark, 0, hard_limit + 1) for mark in punctuation)
    if split_at >= min_index:
        return split_at + 1
    return hard_limit


def _index_for_visual_width(text: str, max_units: int) -> int:
    total = 0
    for index, char in enumerate(text):
        total += _char_width(char)
        if total > max_units:
            return index
    return len(text)


def _visual_width(text: str) -> int:
    return sum(_char_width(char) for char in text)


def _char_width(char: str) -> int:
    if unicodedata.east_asian_width(char) in {"F", "W"}:
        return 2
    return 1


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
