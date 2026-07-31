from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any

from .io_utils import read_json_file, write_json_atomic


def generate_highlight_cut(job_dir: Path, *, target_seconds: float = 60.0, force: bool = False) -> dict[str, Any]:
    output_path = job_dir / "highlight_cut.json"
    if output_path.exists() and not force:
        cached = read_json_file(output_path)
        if cached is not None:
            return cached
    target = max(5.0, float(target_seconds or 60.0))
    cuts = read_json_file(job_dir / "cuts.json")
    if not isinstance(cuts, dict):
        raise RuntimeError("cuts.json is missing or invalid")
    semantic_candidates = _semantic_candidates(job_dir, cuts)
    candidate_source = "semantic_highlights" if semantic_candidates else "structural_clips"
    candidates = semantic_candidates or [
        _candidate_clip(clip)
        for clip in cuts.get("clips", [])
        if isinstance(clip, dict) and clip.get("keep", True) is not False
    ]
    minimum_candidate_seconds = min(3.0, target)
    candidates = [clip for clip in candidates if clip["duration"] >= minimum_candidate_seconds]
    if not candidates:
        raise RuntimeError("cuts.json has no kept clips")
    selected = _select_clips(candidates, target)
    selected_by_time = sorted(selected, key=lambda clip: clip["start"])
    duration = round(sum(clip["duration"] for clip in selected_by_time), 3)
    payload = {
        "status": "ready",
        "target_seconds": round(target, 3),
        "duration_seconds": duration,
        "selected_clip_count": len(selected_by_time),
        "clips": selected_by_time,
        "selection": {
            "method": (
                "semantic_interval_score_desc_until_target_duration"
                if candidate_source == "semantic_highlights"
                else "final_score_desc_until_target_duration"
            ),
            "score_field": "final_score",
            "candidate_source": candidate_source,
            "note": (
                "Precise semantic intervals are preferred when available. Candidates are ranked by score, "
                "kept within the target duration, then restored to timeline order for rendering."
            ),
        },
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    write_json_atomic(output_path, payload)
    return payload


def _candidate_clip(clip: dict[str, Any]) -> dict[str, Any]:
    start = round(max(0.0, _finite_number(clip.get("start"), default=0.0)), 3)
    end = round(max(start, _finite_number(clip.get("end"), default=start)), 3)
    duration = round(max(0.0, end - start), 3)
    score = round(
        _finite_number(
            clip.get("final_score") if clip.get("final_score") is not None else clip.get("content_score"),
            default=0.0,
        ),
        1,
    )
    return {
        "start": start,
        "end": end,
        "duration": duration,
        "final_score": score,
        "content_score": clip.get("content_score"),
        "semantic_score": clip.get("semantic_score", 0),
        "semantic_reasons": [str(item) for item in clip.get("semantic_reasons", []) if str(item).strip()] if isinstance(clip.get("semantic_reasons"), list) else [],
        "reason": str(clip.get("reason") or ""),
        "transcript_text": str(clip.get("subtitle_text") or clip.get("transcript_text") or ""),
    }


def _semantic_candidates(job_dir: Path, cuts: dict[str, Any]) -> list[dict[str, Any]]:
    highlights_payload = read_json_file(job_dir / "highlights.json") or {}
    semantic = highlights_payload.get("highlights")
    if not isinstance(semantic, list) or not semantic:
        semantic = cuts.get("semantic_highlights")
    if not isinstance(semantic, list) or not semantic:
        return []
    transcript = read_json_file(job_dir / "transcript.json") or {}
    raw_segments = transcript.get("segments") if isinstance(transcript.get("segments"), list) else []
    segments = [item for item in raw_segments if isinstance(item, dict)]
    structural_clips = [
        item for item in cuts.get("clips", [])
        if isinstance(item, dict) and item.get("keep", True) is not False
    ]
    candidates = []
    for item in semantic:
        if not isinstance(item, dict):
            continue
        start = _finite_number(item.get("start"), default=-1.0)
        end = _finite_number(item.get("end"), default=-1.0)
        if start < 0 or end <= start:
            continue
        score = max(0.0, min(100.0, _finite_number(item.get("score"), default=0.0)))
        reason = str(item.get("reason") or "").strip()
        recommended_use = str(item.get("recommended_use") or "").strip()
        structure_score = _overlapping_structure_score(start, end, structural_clips)
        candidates.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "final_score": round(score, 1),
            "content_score": round(structure_score, 1),
            "semantic_score": round(score, 1),
            "semantic_reasons": [reason] if reason else [],
            "reason": reason,
            "recommended_use": recommended_use,
            "transcript_text": _transcript_text(segments, start, end),
        })
    return candidates


def _overlapping_structure_score(
    start: float,
    end: float,
    clips: list[dict[str, Any]],
) -> float:
    scores = []
    for clip in clips:
        clip_start = _finite_number(clip.get("start"), default=-1.0)
        clip_end = _finite_number(clip.get("end"), default=-1.0)
        if clip_start < end and clip_end > start:
            scores.append(
                _finite_number(
                    clip.get("final_score")
                    if clip.get("final_score") is not None
                    else clip.get("content_score"),
                    default=0.0,
                )
            )
    return max(scores, default=0.0)


def _transcript_text(
    segments: list[dict[str, Any]],
    start: float,
    end: float,
    *,
    max_chars: int = 1200,
) -> str:
    texts = []
    for segment in segments:
        segment_start = _finite_number(segment.get("start"), default=-1.0)
        segment_end = _finite_number(segment.get("end"), default=-1.0)
        if segment_start < end and segment_end > start:
            text = str(segment.get("text") or "").strip()
            if text:
                texts.append(text)
    return " ".join(texts)[:max_chars]


def _select_clips(clips: list[dict[str, Any]], target_seconds: float) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    total = 0.0
    ranked = sorted(
        clips,
        key=lambda clip: (
            _finite_number(clip.get("final_score"), default=0.0),
            -_finite_number(clip.get("duration"), default=0.0),
        ),
        reverse=True,
    )
    for index, clip in enumerate(ranked, start=1):
        if total >= target_seconds:
            break
        duration = _finite_number(clip.get("duration"), default=0.0)
        if duration <= 0 or duration > target_seconds:
            continue
        if total + duration > target_seconds:
            continue
        if any(_clips_overlap(clip, existing) for existing in selected):
            continue
        value = dict(clip)
        value["selection_rank"] = index
        selected.append(value)
        total += duration
    if not selected and ranked:
        value = _trim_clip_to_duration(ranked[0], target_seconds)
        value["selection_rank"] = 1
        selected.append(value)
    return selected


def _clips_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_start = _finite_number(left.get("start"), default=0.0)
    left_end = _finite_number(left.get("end"), default=left_start)
    right_start = _finite_number(right.get("start"), default=0.0)
    right_end = _finite_number(right.get("end"), default=right_start)
    return min(left_end, right_end) - max(left_start, right_start) > 0.05


def _trim_clip_to_duration(clip: dict[str, Any], target_seconds: float) -> dict[str, Any]:
    value = dict(clip)
    start = _finite_number(value.get("start"), default=0.0)
    end = _finite_number(value.get("end"), default=start)
    duration = max(0.0, end - start)
    if duration <= target_seconds:
        value["duration"] = round(duration, 3)
        return value
    trim_start = start + (duration - target_seconds) / 2
    value["start"] = round(trim_start, 3)
    value["end"] = round(trim_start + target_seconds, 3)
    value["duration"] = round(target_seconds, 3)
    value["trimmed_to_target"] = True
    return value


def _finite_number(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default
