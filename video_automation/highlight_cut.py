from __future__ import annotations

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
    candidates = [_candidate_clip(clip) for clip in cuts.get("clips", []) if isinstance(clip, dict) and clip.get("keep", True) is not False]
    candidates = [clip for clip in candidates if clip["duration"] > 0]
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
            "method": "final_score_desc_until_target_duration",
            "score_field": "final_score",
            "note": "Clips are selected by recommendation score, then restored to timeline order for rendering.",
        },
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    write_json_atomic(output_path, payload)
    return payload


def _candidate_clip(clip: dict[str, Any]) -> dict[str, Any]:
    start = round(max(0.0, float(clip.get("start") or 0.0)), 3)
    end = round(max(start, float(clip.get("end") or start)), 3)
    duration = round(max(0.0, float(clip.get("duration") or end - start)), 3)
    score = round(float(clip.get("final_score") if clip.get("final_score") is not None else clip.get("content_score") or 0.0), 1)
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


def _select_clips(clips: list[dict[str, Any]], target_seconds: float) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    total = 0.0
    ranked = sorted(clips, key=lambda clip: (float(clip.get("final_score") or 0), float(clip.get("duration") or 0)), reverse=True)
    for index, clip in enumerate(ranked, start=1):
        if selected and total >= target_seconds:
            break
        duration = float(clip.get("duration") or 0)
        if selected and total + duration > target_seconds:
            continue
        value = dict(clip)
        value["selection_rank"] = index
        selected.append(value)
        total += duration
    return selected
