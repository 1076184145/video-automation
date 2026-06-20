from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .io_utils import read_json_file, write_json_atomic, write_text_atomic
from .llm_tools import call_structured_llm
from .subtitles import (
    ass_document,
    kept_clips,
    play_resolution,
    prepare_subtitle_segments,
    remap_segments_to_clips,
    segments_from_transcript,
)


TARGET_LANGUAGE_LABELS = {
    "zh": "Simplified Chinese",
    "en": "English",
    "ko": "Korean",
    "ja": "Japanese",
}
MAX_TRANSLATION_SEGMENTS = 1200
MAX_TRANSLATION_CHARS = 240_000


def translate_subtitles(
    settings: Settings,
    job_dir: Path,
    *,
    target_language: str = "zh",
    force: bool = False,
) -> dict[str, Any]:
    target = _target_code(target_language)
    output_path = job_dir / f"transcript_translated_{target}.json"
    if output_path.exists() and not force:
        cached = read_json_file(output_path)
        if cached is not None:
            return cached

    transcript = read_json_file(job_dir / "transcript.json") or {}
    source_segments = segments_from_transcript(transcript)
    if not source_segments:
        raise RuntimeError("transcript.json has no segments to translate")
    _validate_translation_workload(source_segments)

    translated_segments = []
    for batch in _segment_batches(
        source_segments,
        max_segments=settings.llm_translation_batch_size,
        max_chars=settings.llm_translation_batch_chars,
    ):
        translated_segments.extend(_translate_batch(settings, batch, target))
    translated_segments = _merge_translations(source_segments, translated_segments)
    payload = {
        "status": "ready",
        "backend": settings.llm_provider,
        "model": settings.llm_model,
        "source_language": transcript.get("language"),
        "target_language": target,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "text": "\n".join(segment["text"] for segment in translated_segments),
        "segments": translated_segments,
    }
    write_json_atomic(output_path, payload)
    _write_translated_text_outputs(job_dir, target, translated_segments)
    _write_translated_ass_outputs(settings, job_dir, target, payload)
    return payload


def translated_clipped_ass_name(target_language: str) -> str:
    return f"subtitles_translated_{_target_code(target_language)}_clipped.ass"


def translated_final_video_name(target_language: str) -> str:
    return f"final_translated_{_target_code(target_language)}.mp4"


def _translate_batch(settings: Settings, segments: list[dict[str, Any]], target: str) -> list[dict[str, Any]]:
    payload = call_structured_llm(
        settings,
        system=(
            "You are a professional subtitle translator for short videos. "
            "Translate each subtitle segment faithfully and naturally. "
            "Preserve names and platform slang where appropriate. "
            "Return only the requested JSON."
        ),
        user=json.dumps(
            {
                "target_language": TARGET_LANGUAGE_LABELS.get(target, target),
                "requirements": [
                    "Translate text only; do not change start/end times.",
                    "Keep each translated segment concise enough for subtitles.",
                    "Do not add explanations, notes, or speaker labels.",
                    "If source text is meaningless filler such as repeated punctuation, translate it to an empty string.",
                ],
                "segments": [
                    {
                        "id": index,
                        "start": segment["start"],
                        "end": segment["end"],
                        "text": segment["text"],
                    }
                    for index, segment in enumerate(segments)
                ],
            },
            ensure_ascii=False,
        ),
        schema=_translation_schema(),
        schema_name="subtitle_translation",
    )
    items = payload.get("translations", [])
    if not isinstance(items, list):
        raise RuntimeError("LLM translation response is missing translations")
    translated = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(segments):
            translated.append({
                "start": float(segments[index]["start"]),
                "end": float(segments[index]["end"]),
                "text": str(item.get("text") or "").strip(),
            })
    return translated


def _translation_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["translations"],
        "properties": {
            "translations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "text"],
                    "properties": {
                        "id": {"type": "integer"},
                        "text": {"type": "string"},
                    },
                },
            }
        },
    }


def _validate_translation_workload(segments: list[dict[str, Any]]) -> None:
    total_chars = sum(len(str(segment.get("text") or "")) for segment in segments)
    if len(segments) > MAX_TRANSLATION_SEGMENTS:
        raise RuntimeError(
            f"subtitle translation is limited to {MAX_TRANSLATION_SEGMENTS} segments per run; "
            "split the job or reduce transcript segments first"
        )
    if total_chars > MAX_TRANSLATION_CHARS:
        raise RuntimeError(
            f"subtitle translation is limited to {MAX_TRANSLATION_CHARS} source characters per run; "
            "split the job or reduce transcript length first"
        )


def _segment_batches(segments: list[dict[str, Any]], *, max_segments: int = 24, max_chars: int = 6000) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for segment in segments:
        text_len = len(str(segment.get("text") or ""))
        if current and (len(current) >= max_segments or current_chars + text_len > max_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(segment)
        current_chars += text_len
    if current:
        batches.append(current)
    return batches


def _merge_translations(source_segments: list[dict[str, Any]], translated_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_time = {(round(float(item["start"]), 3), round(float(item["end"]), 3)): item for item in translated_segments}
    merged = []
    for source in source_segments:
        key = (round(float(source["start"]), 3), round(float(source["end"]), 3))
        translated = by_time.get(key)
        text = str(translated.get("text") if translated else source.get("text") or "").strip()
        if not text:
            continue
        merged.append({"start": float(source["start"]), "end": float(source["end"]), "text": text})
    return merged


def _write_translated_text_outputs(job_dir: Path, target: str, segments: list[dict[str, Any]]) -> None:
    base = f"transcript_translated_{target}"
    write_text_atomic(job_dir / f"{base}.txt", "\n".join(segment["text"] for segment in segments))
    write_text_atomic(job_dir / f"{base}.srt", _srt_document(segments))


def _write_translated_ass_outputs(settings: Settings, job_dir: Path, target: str, transcript: dict[str, Any]) -> None:
    segments = prepare_subtitle_segments(settings, segments_from_transcript(transcript))
    write_text_atomic(
        job_dir / f"subtitles_translated_{target}.ass",
        ass_document(settings, segments, play_resolution(job_dir)),
    )
    cuts = read_json_file(job_dir / "cuts.json") or {}
    clipped_segments = prepare_subtitle_segments(
        settings,
        remap_segments_to_clips(segments_from_transcript(transcript), kept_clips(cuts)),
    )
    write_text_atomic(
        job_dir / f"subtitles_translated_{target}_clipped.ass",
        ass_document(settings, clipped_segments, play_resolution(job_dir)),
    )


def _srt_document(segments: list[dict[str, Any]]) -> str:
    blocks = []
    for index, segment in enumerate(segments, 1):
        blocks.append(
            f"{index}\n"
            f"{_srt_time(float(segment['start']))} --> {_srt_time(float(segment['end']))}\n"
            f"{segment['text']}\n"
        )
    return "\n".join(blocks)


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    ms = milliseconds % 1000
    total_seconds = milliseconds // 1000
    sec = total_seconds % 60
    total_minutes = total_seconds // 60
    minute = total_minutes % 60
    hour = total_minutes // 60
    return f"{hour:02d}:{minute:02d}:{sec:02d},{ms:03d}"


def _target_code(language: str) -> str:
    value = language.strip().lower().replace("_", "-")
    aliases = {
        "chinese": "zh",
        "zh-cn": "zh",
        "cn": "zh",
        "mandarin": "zh",
        "english": "en",
        "korean": "ko",
        "japanese": "ja",
    }
    value = aliases.get(value, value)
    if value not in TARGET_LANGUAGE_LABELS:
        raise RuntimeError(f"unsupported subtitle translation target: {language}")
    return value
