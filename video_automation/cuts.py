from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import native_cuts
from .config import Settings
from .io_utils import read_json_file, write_json_atomic, write_text_atomic

logger = logging.getLogger(__name__)

def generate_cuts(
    job_dir: Path,
    duration: float,
    *,
    force: bool = False,
    min_clip_seconds: float = 2.0,
    merge_gap_seconds: float = 1.5,
) -> dict[str, Any]:
    cuts_json = job_dir / "cuts.json"
    cuts_md = job_dir / "cuts.md"
    if cuts_json.exists() and cuts_md.exists() and not force:
        cached = read_json_file(cuts_json)
        if cached is not None:
            return cached

    silence_payload = _read_json(job_dir / "silence.json")
    freeze_payload = _read_json(job_dir / "freeze.json")
    scene_payload = _read_json(job_dir / "scene.json")
    transcript_payload = _read_json(job_dir / "transcript.json")

    settings = Settings.load()
    native_enabled = getattr(settings, "native_cuts_enabled", True)

    invalid_segments = build_invalid_segments(duration, silence_payload, freeze_payload, native_enabled=native_enabled)

    clips = None
    if native_enabled:
        try:
            clips = native_cuts.generate_and_stabilize_clips(
                duration, invalid_segments, silence_payload.get("min_gap_seconds", 0.35), min_clip_seconds, merge_gap_seconds
            )
        except Exception as e:
            logger.warning(f"Native clips generation failed: {e}. Falling back to Python.")
            clips = None

    if clips is None:
        clips = _clips_from_invalid_segments(
            duration,
            invalid_segments,
            silence_payload.get("min_gap_seconds", 0.35),
            min_clip_seconds=min_clip_seconds,
            merge_gap_seconds=merge_gap_seconds,
        )

    transcript_segments = _summarize_segments(transcript_payload)
    scenes = _summarize_scenes(scene_payload, duration)
    clips = _attach_scenes_to_clips(clips, scenes)

    scored_clips = None
    if native_enabled:
        try:
            scored_clips = native_cuts.attach_transcript_and_score(clips, transcript_segments)
        except Exception as e:
            logger.warning(f"Native scoring failed: {e}. Falling back to Python.")
            scored_clips = None

    if scored_clips is None:
        clips = _attach_transcript_to_clips(clips, transcript_segments)
        clips = _score_content_value(clips)
    else:
        clips = scored_clips

    semantic_highlights = _semantic_highlights(job_dir)
    clips = _attach_semantic_highlights(clips, semantic_highlights)
    payload = {
        "status": "needs_review",
        "duration_seconds": duration,
        "source": _cut_source(silence_payload, freeze_payload, invalid_segments),
        "invalid_segments": invalid_segments,
        "highlight_signals": {
            "scenes": scenes,
            "scene_count": len(scenes),
            "semantic_highlight_count": len(semantic_highlights),
        },
        "semantic_highlights": semantic_highlights,
        "content_scoring": {
            "method": "0.4*structure_score+0.6*semantic_score" if semantic_highlights else "speech_density+scene_density+duration_balance",
            "note": "Final scores rank clips for review; they do not auto-delete or reorder media.",
        },
        "clip_stabilization": {
            "min_clip_seconds": min_clip_seconds,
            "merge_gap_seconds": merge_gap_seconds,
            "note": "Adjacent kept clips separated by short gaps are merged to reduce jump cuts.",
        },
        "clips": clips,
        "transcript_segments": transcript_segments,
        "notes": [
            "Review clips before final rendering.",
            "This file is advisory only; no source media has been cut.",
            "When silence.json and freeze.json both exist, only overlapping silent and static spans are treated as invalid.",
            "Scene changes now contribute to content_score, but they do not remove media by themselves.",
        ],
    }
    write_json_atomic(cuts_json, payload)
    write_text_atomic(cuts_md, _render_markdown(payload))
    return payload


def update_cuts_from_editor(job_dir: Path, clips: list[dict[str, Any]]) -> dict[str, Any]:
    cuts_json = job_dir / "cuts.json"
    current = read_json_file(cuts_json)
    if not isinstance(current, dict):
        raise RuntimeError("cuts.json is missing or invalid")

    duration = float(current.get("duration_seconds") or 0.0)
    edited_clips = _validate_editor_clips(clips, duration)
    transcript_segments = current.get("transcript_segments") if isinstance(current.get("transcript_segments"), list) else []
    scenes = current.get("highlight_signals", {}).get("scenes", [])

    settings = Settings.load()
    native_enabled = getattr(settings, "native_cuts_enabled", True)

    edited_clips = _attach_scenes_to_clips(edited_clips, scenes if isinstance(scenes, list) else [])

    scored_clips = None
    if native_enabled:
        try:
            scored_clips = native_cuts.attach_transcript_and_score(edited_clips, transcript_segments)
        except Exception as e:
            logger.warning(f"Native editor scoring failed: {e}. Falling back to Python.")
            scored_clips = None

    if scored_clips is None:
        edited_clips = _attach_transcript_to_clips(edited_clips, transcript_segments)
        edited_clips = _score_content_value(edited_clips)
    else:
        edited_clips = scored_clips

    semantic_highlights = _semantic_highlights(job_dir, current)
    edited_clips = _attach_semantic_highlights(edited_clips, semantic_highlights)

    payload = dict(current)
    payload["status"] = "needs_review"
    payload["source"] = "manual_edit"
    payload["clips"] = edited_clips
    payload["semantic_highlights"] = semantic_highlights
    payload["content_scoring"] = {
        "method": "0.4*structure_score+0.6*semantic_score" if semantic_highlights else "speech_density+scene_density+duration_balance",
        "note": "Final scores rank clips for review; they do not auto-delete or reorder media.",
    }
    notes = [note for note in payload.get("notes", []) if isinstance(note, str)]
    if "Clips were edited in the Web UI." not in notes:
        notes.append("Clips were edited in the Web UI.")
    payload["notes"] = notes
    write_json_atomic(cuts_json, payload)
    write_text_atomic(job_dir / "cuts.md", _render_markdown(payload))
    return payload


def _validate_editor_clips(clips: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    if not isinstance(clips, list) or not clips:
        raise RuntimeError("clips must be a non-empty list")
    normalized = []
    for index, clip in enumerate(clips, start=1):
        if not isinstance(clip, dict):
            raise RuntimeError(f"clip {index} is invalid")
        try:
            start = round(max(0.0, float(clip["start"])), 3)
            end = round(float(clip["end"]), 3)
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"clip {index} start/end is invalid") from exc
        if duration > 0:
            end = min(duration, end)
        if end <= start:
            raise RuntimeError(f"clip {index} end must be greater than start")
        normalized.append({
            "start": start,
            "end": end,
            "duration": round(end - start, 3),
            "keep": bool(clip.get("keep", True)),
            "reason": str(clip.get("reason") or "manual edit"),
            **_editor_subtitle_override(clip),
        })
    return sorted(normalized, key=lambda item: item["start"])


def _editor_subtitle_override(clip: dict[str, Any]) -> dict[str, Any]:
    if not clip.get("subtitle_override"):
        return {}
    text = str(clip.get("subtitle_text") or clip.get("transcript_text") or "").strip()
    return {
        "subtitle_override": True,
        "subtitle_text": text,
        "transcript_text": _truncate_text(text, 160),
    }


def _read_json(path: Path) -> dict[str, Any]:
    return read_json_file(path) or {}


def build_invalid_segments(duration: float, silence_payload: dict[str, Any], freeze_payload: dict[str, Any], native_enabled: bool = False) -> list[dict[str, Any]]:
    if native_enabled:
        try:
            return native_cuts.merge_invalid_ranges(
                duration,
                silence_payload.get("silences", []),
                freeze_payload.get("freezes", [])
            )
        except Exception as e:
            logger.warning(f"Native invalid segments merge failed: {e}. Falling back to Python.")

    silences = _valid_ranges(silence_payload.get("silences", []), duration)
    freezes = _valid_ranges(freeze_payload.get("freezes", []), duration)
    if silences and freezes:
        return _merge_ranges(_intersect_ranges(silences, freezes), "silence+freeze")
    if silences:
        return _merge_ranges(silences, "silence")
    return []


def _valid_ranges(ranges: list[dict[str, Any]], duration: float) -> list[dict[str, float]]:
    valid = []
    for item in ranges:
        try:
            start = max(0.0, float(item["start"]))
            end = min(duration, float(item["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        valid.append({"start": round(start, 3), "end": round(end, 3), "duration": round(end - start, 3)})
    return sorted(valid, key=lambda item: item["start"])


def _intersect_ranges(left: list[dict[str, float]], right: list[dict[str, float]]) -> list[dict[str, float]]:
    intersections = []
    left_index = 0
    right_index = 0
    while left_index < len(left) and right_index < len(right):
        left_item = left[left_index]
        right_item = right[right_index]
        start = max(left_item["start"], right_item["start"])
        end = min(left_item["end"], right_item["end"])
        if end > start:
            intersections.append({"start": round(start, 3), "end": round(end, 3), "duration": round(end - start, 3)})
        if left_item["end"] < right_item["end"]:
            left_index += 1
        else:
            right_index += 1
    return intersections


def _merge_ranges(ranges: list[dict[str, float]], reason: str, *, gap_seconds: float = 0.12, min_duration: float = 0.35) -> list[dict[str, Any]]:
    if not ranges:
        return []
    merged = []
    current = dict(ranges[0])
    for item in ranges[1:]:
        if item["start"] <= current["end"] + gap_seconds:
            current["end"] = max(current["end"], item["end"])
            current["duration"] = round(current["end"] - current["start"], 3)
            continue
        if current["duration"] >= min_duration:
            merged.append(_invalid_segment(current["start"], current["end"], reason))
        current = dict(item)
    if current["duration"] >= min_duration:
        merged.append(_invalid_segment(current["start"], current["end"], reason))
    return merged


def _invalid_segment(start: float, end: float, reason: str) -> dict[str, Any]:
    return {
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(max(0.0, end - start), 3),
        "drop": True,
        "reason": reason,
    }


def _clips_from_invalid_segments(
    duration: float,
    invalid_segments: list[dict[str, Any]],
    min_gap: float,
    *,
    min_clip_seconds: float,
    merge_gap_seconds: float,
) -> list[dict[str, Any]]:
    if duration <= 0:
        return []
    if not invalid_segments:
        return [{"start": 0.0, "end": round(duration, 3), "duration": round(duration, 3), "keep": True, "reason": "full media"}]
    clips: list[dict[str, Any]] = []
    cursor = 0.0
    padding = max(0.0, min_gap / 2)
    for segment in invalid_segments:
        start = max(0.0, float(segment.get("start", 0.0)) - padding)
        if start > cursor:
            clips.append(_clip(cursor, start, f"before {segment.get('reason', 'invalid segment')}"))
        cursor = min(duration, float(segment.get("end", start)) + padding)
    if cursor < duration:
        clips.append(_clip(cursor, duration, "tail"))
    clips = [clip for clip in clips if clip["duration"] >= 0.35]
    return _stabilize_keep_clips(clips, min_clip_seconds=min_clip_seconds, merge_gap_seconds=merge_gap_seconds)


def _stabilize_keep_clips(clips: list[dict[str, Any]], *, min_clip_seconds: float, merge_gap_seconds: float) -> list[dict[str, Any]]:
    if len(clips) <= 1:
        return clips
    merged = _merge_clips_across_short_gaps(clips, merge_gap_seconds)
    merged = _absorb_short_clips(merged, min_clip_seconds, max_gap=max(merge_gap_seconds, min_clip_seconds * 1.5))
    return _merge_clips_across_short_gaps(merged, merge_gap_seconds)


def _merge_clips_across_short_gaps(clips: list[dict[str, Any]], merge_gap_seconds: float) -> list[dict[str, Any]]:
    if not clips:
        return []
    merged = [dict(clips[0])]
    for clip in clips[1:]:
        current = dict(clip)
        previous = merged[-1]
        gap = max(0.0, float(current["start"]) - float(previous["end"]))
        if gap <= merge_gap_seconds:
            previous["end"] = current["end"]
            previous["duration"] = round(float(previous["end"]) - float(previous["start"]), 3)
            previous["reason"] = _join_reasons(previous.get("reason"), current.get("reason"), f"merged gap {gap:.2f}s")
            continue
        merged.append(current)
    return merged


def _absorb_short_clips(clips: list[dict[str, Any]], min_clip_seconds: float, max_gap: float) -> list[dict[str, Any]]:
    if len(clips) <= 1:
        return clips
    result: list[dict[str, Any]] = []
    index = 0
    while index < len(clips):
        current = dict(clips[index])
        if float(current["duration"]) >= min_clip_seconds:
            result.append(current)
            index += 1
            continue
        left = result[-1] if result else None
        right = dict(clips[index + 1]) if index + 1 < len(clips) else None
        left_gap = float(current["start"]) - float(left["end"]) if left else float("inf")
        right_gap = float(right["start"]) - float(current["end"]) if right else float("inf")
        if left and (left_gap <= right_gap or not right) and left_gap <= max_gap:
            left["end"] = current["end"]
            left["duration"] = round(float(left["end"]) - float(left["start"]), 3)
            left["reason"] = _join_reasons(left.get("reason"), current.get("reason"), "absorbed short clip")
            index += 1
            continue
        if right and right_gap <= max_gap:
            right["start"] = current["start"]
            right["duration"] = round(float(right["end"]) - float(right["start"]), 3)
            right["reason"] = _join_reasons(current.get("reason"), right.get("reason"), "absorbed short clip")
            clips[index + 1] = right
            index += 1
            continue
        result.append(current)
        index += 1
    return result


def _join_reasons(*parts: Any) -> str:
    values = []
    for part in parts:
        value = str(part or "").strip()
        if value and value not in values:
            values.append(value)
    return " / ".join(values) if values else "merged clip"


def _cut_source(silence_payload: dict[str, Any], freeze_payload: dict[str, Any], invalid_segments: list[dict[str, Any]]) -> str:
    if silence_payload and freeze_payload:
        return "silence+freeze" if invalid_segments else "full_media"
    if silence_payload:
        return "silence.json" if invalid_segments else "full_media"
    return "full_media"


def _clip(start: float, end: float, reason: str) -> dict[str, Any]:
    return {
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(max(0.0, end - start), 3),
        "keep": True,
        "reason": reason,
    }


def _summarize_segments(transcript_payload: dict[str, Any]) -> list[dict[str, Any]]:
    segments = transcript_payload.get("segments")
    if not isinstance(segments, list):
        return []
    summarized = []
    for segment in segments[:200]:
        summarized.append({
            "start": segment.get("start"),
            "end": segment.get("end"),
            "text": str(segment.get("text", "")).strip(),
            "words": segment.get("words") if isinstance(segment.get("words"), list) else [],
        })
    return summarized


def _attach_transcript_to_clips(clips: list[dict[str, Any]], transcript_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = []
    for clip in clips:
        overlapping = _segments_for_clip(clip, transcript_segments)
        text = _clip_text_from_segments(clip, overlapping)
        value = dict(clip)
        if value.get("subtitle_override"):
            value["subtitle_text"] = str(value.get("subtitle_text") or value.get("transcript_text") or "").strip()
            value["transcript_text"] = _truncate_text(value["subtitle_text"], 160)
        else:
            value["transcript_text"] = _truncate_text(text, 160)
        value["transcript_segments"] = overlapping[:20]
        enriched.append(value)
    return enriched


def _clip_text_from_segments(clip: dict[str, Any], segments: list[dict[str, Any]]) -> str:
    try:
        clip_start = float(clip["start"])
        clip_end = float(clip["end"])
    except (KeyError, TypeError, ValueError):
        return ""
    parts = []
    for segment in segments:
        words = segment.get("words")
        if isinstance(words, list) and words:
            selected = []
            for word in words:
                if not isinstance(word, dict):
                    continue
                try:
                    start = float(word["start"])
                    end = float(word["end"])
                except (KeyError, TypeError, ValueError):
                    continue
                if end <= clip_start or start >= clip_end:
                    continue
                selected.append(str(word.get("word", "")).strip())
            if selected:
                parts.append("".join(selected))
                continue
        if segment.get("text"):
            parts.append(str(segment["text"]).strip())
    return " ".join(part for part in parts if part).strip()


def _summarize_scenes(scene_payload: dict[str, Any], duration: float) -> list[dict[str, Any]]:
    scenes = []
    for scene in scene_payload.get("scenes", []):
        try:
            time_value = float(scene["time"])
        except (KeyError, TypeError, ValueError):
            continue
        if time_value < 0 or time_value > duration:
            continue
        scenes.append({"time": round(time_value, 3), "reason": str(scene.get("reason", "scene_change"))})
    return sorted(scenes, key=lambda scene: scene["time"])


def _attach_scenes_to_clips(clips: list[dict[str, Any]], scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = []
    for clip in clips:
        matches = _scenes_for_clip(clip, scenes)
        value = dict(clip)
        value["scene_count"] = len(matches)
        value["scene_times"] = [scene["time"] for scene in matches]
        enriched.append(value)
    return enriched


def _score_content_value(clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored = []
    for clip in clips:
        value = dict(clip)
        duration = max(0.001, float(value.get("duration") or 0.001))
        text = str(value.get("transcript_text") or "")
        scene_count = int(value.get("scene_count") or 0)
        speech_density = min(1.0, len(text) / max(28.0, duration * 7.0))
        scene_density = min(1.0, scene_count / max(1.0, duration / 18.0))
        duration_balance = 1.0 if 8 <= duration <= 75 else 0.55 if 4 <= duration <= 120 else 0.25
        score = round((speech_density * 0.55 + scene_density * 0.3 + duration_balance * 0.15) * 100, 1)
        value["content_score"] = score
        value["content_signals"] = {
            "speech_density": round(speech_density, 3),
            "scene_density": round(scene_density, 3),
            "duration_balance": round(duration_balance, 3),
        }
        value["recommendation"] = "strong_keep" if score >= 70 else "review" if score >= 42 else "trim_candidate"
        scored.append(value)
    ranked = sorted(scored, key=lambda item: float(item.get("content_score") or 0), reverse=True)
    ranks = {id(item): index for index, item in enumerate(ranked, start=1)}
    for item in scored:
        item["content_rank"] = ranks[id(item)]
    return scored


def _semantic_highlights(job_dir: Path, current: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    payload = read_json_file(job_dir / "highlights.json")
    if not isinstance(payload, dict) and isinstance(current, dict):
        existing = current.get("semantic_highlights")
        return [dict(item) for item in existing if isinstance(item, dict)] if isinstance(existing, list) else []
    items = payload.get("highlights") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    highlights: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            start = round(max(0.0, float(item.get("start"))), 3)
            end = round(max(start, float(item.get("end"))), 3)
            score = round(max(0.0, min(100.0, float(item.get("score") or 0.0))), 1)
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        highlights.append({
            "start": start,
            "end": end,
            "score": score,
            "reason": str(item.get("reason") or "").strip(),
            "recommended_use": str(item.get("recommended_use") or "").strip(),
        })
    return sorted(highlights, key=lambda item: item["start"])


def _attach_semantic_highlights(clips: list[dict[str, Any]], highlights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = []
    for clip in clips:
        value = dict(clip)
        matches = _semantic_matches_for_clip(value, highlights)
        semantic_score = max((float(item.get("score") or 0.0) for item in matches), default=0.0)
        structure_score = float(value.get("content_score") or 0.0)
        value["semantic_score"] = round(semantic_score, 1)
        value["semantic_reasons"] = [str(item.get("reason") or "").strip() for item in matches if str(item.get("reason") or "").strip()][:3]
        value["semantic_recommended_use"] = [str(item.get("recommended_use") or "").strip() for item in matches if str(item.get("recommended_use") or "").strip()][:3]
        value["final_score"] = round(structure_score * 0.4 + semantic_score * 0.6, 1) if highlights else round(structure_score, 1)
        value["recommendation"] = "strong_keep" if value["final_score"] >= 70 else "review" if value["final_score"] >= 42 else "trim_candidate"
        enriched.append(value)
    ranked = sorted(enriched, key=lambda item: float(item.get("final_score") or 0), reverse=True)
    ranks = {id(item): index for index, item in enumerate(ranked, start=1)}
    for item in enriched:
        item["final_rank"] = ranks[id(item)]
    return enriched


def _semantic_matches_for_clip(clip: dict[str, Any], highlights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        start = float(clip.get("start") or 0.0)
        end = float(clip.get("end") or 0.0)
    except (TypeError, ValueError):
        return []
    matches = []
    for item in highlights:
        try:
            item_start = float(item.get("start") or 0.0)
            item_end = float(item.get("end") or 0.0)
        except (TypeError, ValueError):
            continue
        if item_start < end and item_end > start:
            matches.append(item)
    return sorted(matches, key=lambda item: float(item.get("score") or 0.0), reverse=True)


def _scenes_for_clip(clip: dict[str, Any], scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        clip_start = float(clip["start"])
        clip_end = float(clip["end"])
    except (KeyError, TypeError, ValueError):
        return []
    return [scene for scene in scenes if clip_start <= float(scene["time"]) < clip_end]


def _segments_for_clip(clip: dict[str, Any], transcript_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        clip_start = float(clip["start"])
        clip_end = float(clip["end"])
    except (KeyError, TypeError, ValueError):
        return []
    matches = []
    for segment in transcript_segments:
        try:
            start = float(segment["start"])
            end = float(segment["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= clip_start or start >= clip_end:
            continue
        matches.append(segment)
    return matches


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Cut Review",
        "",
        f"- Source: {payload['source']}",
        f"- Duration: {payload['duration_seconds']:.2f}s",
        f"- Invalid segment count: {len(payload['invalid_segments'])}",
        f"- Scene change count: {payload['highlight_signals']['scene_count']}",
        f"- Clip count: {len(payload['clips'])}",
        f"- Content scoring: {payload.get('content_scoring', {}).get('method', 'n/a')}",
        "",
        "## Highlight Signals",
        "",
        "| # | Time | Reason |",
        "|---|---:|---|",
    ]
    for index, scene in enumerate(payload["highlight_signals"]["scenes"], start=1):
        lines.append(f"| {index} | {scene['time']:.3f} | {scene['reason']} |")
    lines.extend([
        "",
        "## Invalid Segments",
        "",
        "| # | Start | End | Duration | Reason |",
        "|---|---:|---:|---:|---|",
    ])
    for index, segment in enumerate(payload["invalid_segments"], start=1):
        lines.append(f"| {index} | {segment['start']:.3f} | {segment['end']:.3f} | {segment['duration']:.3f} | {segment['reason']} |")
    lines.extend([
        "",
        "## Suggested Clips",
        "",
        "| # | Start | End | Duration | Final | Structure | Semantic | Rank | Scenes | Recommendation | Reason | Content |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|",
    ])
    for index, clip in enumerate(payload["clips"], start=1):
        lines.append(
            f"| {index} | {clip['start']:.3f} | {clip['end']:.3f} | {clip['duration']:.3f} | "
            f"{clip.get('final_score', clip.get('content_score', 0)):.1f} | {clip.get('content_score', 0):.1f} | "
            f"{clip.get('semantic_score', 0):.1f} | {clip.get('final_rank', clip.get('content_rank', ''))} | {clip.get('scene_count', 0)} | "
            f"{clip.get('recommendation', '')} | {clip['reason']} | {_markdown_cell(clip.get('transcript_text', ''))} |"
        )
    lines.extend(["", "## Notes", ""])
    lines.extend(f"- {note}" for note in payload["notes"])
    return "\n".join(lines) + "\n"


def _markdown_cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()
