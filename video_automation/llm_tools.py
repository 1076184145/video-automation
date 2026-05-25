from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .io_utils import read_json_file, write_json_atomic


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
ALLOWED_METADATA_KEYS = {"titles", "descriptions", "tags", "hashtags", "cover_titles", "platform_notes"}


def generate_metadata(settings: Settings, job_dir: Path, *, platform: str = "douyin", force: bool = False) -> dict[str, Any]:
    output_path = job_dir / "metadata.json"
    if output_path.exists() and not force:
        cached = read_json_file(output_path)
        if cached is not None:
            return cached
    payload = _call_structured_llm(
        settings,
        system="You are a Chinese short-video publishing assistant. Return concise, platform-ready metadata.",
        user=_metadata_prompt(job_dir, platform),
        schema=_metadata_schema(),
        schema_name="video_metadata",
    )
    payload.update({
        "status": "ready",
        "backend": settings.llm_provider,
        "model": settings.llm_model,
        "platform": platform,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    })
    write_json_atomic(output_path, payload)
    return payload


def save_metadata(job_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    current = read_json_file(job_dir / "metadata.json") or {}
    updated = dict(current)
    for key in ALLOWED_METADATA_KEYS:
        if key in payload:
            updated[key] = _metadata_list(payload[key])
    updated["edited_in_web"] = True
    updated["updated_at"] = datetime.now().isoformat(timespec="seconds")
    write_json_atomic(job_dir / "metadata.json", updated)
    return updated


def _metadata_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise RuntimeError("metadata fields must be arrays")
    return [str(item).strip() for item in value if str(item).strip()][:50]


def generate_highlights(settings: Settings, job_dir: Path, *, force: bool = False) -> dict[str, Any]:
    output_path = job_dir / "highlights.json"
    if output_path.exists() and not force:
        cached = read_json_file(output_path)
        if cached is not None:
            return cached
    payload = _call_structured_llm(
        settings,
        system="You find semantic highlights in livestream recordings. Use only the provided timestamps.",
        user=_highlights_prompt(job_dir),
        schema=_highlights_schema(),
        schema_name="semantic_highlights",
    )
    payload.update({
        "status": "ready",
        "backend": settings.llm_provider,
        "model": settings.llm_model,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    })
    write_json_atomic(output_path, payload)
    _attach_highlights_to_cuts(job_dir, payload)
    return payload


def _call_structured_llm(settings: Settings, *, system: str, user: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
    provider = settings.llm_provider.strip().lower()
    if provider != "openai":
        raise RuntimeError(f"unsupported LLM_PROVIDER: {settings.llm_provider}")
    if not settings.openai_api_key.strip():
        raise RuntimeError("OPENAI_API_KEY is not configured")
    if not settings.llm_model.strip():
        raise RuntimeError("LLM_MODEL is not configured")
    request_payload = {
        "model": settings.llm_model,
        "input": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": schema,
            }
        },
    }
    request = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(request_payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.openai_api_key.strip()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI request failed: {exc.code} {detail}") from exc
    except OSError as exc:
        raise RuntimeError(f"OpenAI request failed: {exc}") from exc
    text = _extract_output_text(raw)
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise RuntimeError("LLM returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("LLM returned a non-object JSON payload")
    return parsed


def _extract_output_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    parts: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                parts.append(content["text"])
    return "\n".join(parts).strip()


def _metadata_prompt(job_dir: Path, platform: str) -> str:
    manifest = read_json_file(job_dir / "manifest.json") or {}
    transcript = read_json_file(job_dir / "transcript.json") or {}
    cuts = read_json_file(job_dir / "cuts.json") or {}
    top_clips = sorted(cuts.get("clips", []), key=lambda item: float(item.get("content_score") or 0), reverse=True)[:8]
    segments = transcript.get("segments", [])[:80]
    return json.dumps({
        "platform": platform,
        "source_name": manifest.get("source_name") or manifest.get("source_path") or job_dir.name,
        "duration_seconds": manifest.get("duration_seconds"),
        "top_clips": [
            {
                "start": clip.get("start"),
                "end": clip.get("end"),
                "score": clip.get("content_score"),
                "text": clip.get("subtitle_text") or clip.get("transcript_text"),
            }
            for clip in top_clips
        ],
        "transcript_sample": [
            {"start": item.get("start"), "end": item.get("end"), "text": item.get("text")}
            for item in segments
        ],
        "requirements": "Generate Chinese title candidates, descriptions, tags, hashtags, and cover title ideas. Avoid clickbait that misrepresents content.",
    }, ensure_ascii=False)


def _highlights_prompt(job_dir: Path) -> str:
    transcript = read_json_file(job_dir / "transcript.json") or {}
    cuts = read_json_file(job_dir / "cuts.json") or {}
    scene = read_json_file(job_dir / "scene.json") or {}
    clips = sorted(cuts.get("clips", []), key=lambda item: float(item.get("content_score") or 0), reverse=True)[:30]
    segments = transcript.get("segments", [])[:180]
    return json.dumps({
        "candidate_clips": [
            {
                "start": clip.get("start"),
                "end": clip.get("end"),
                "score": clip.get("content_score"),
                "scene_count": clip.get("scene_count"),
                "text": clip.get("subtitle_text") or clip.get("transcript_text"),
            }
            for clip in clips
        ],
        "transcript_sample": [
            {"start": item.get("start"), "end": item.get("end"), "text": item.get("text")}
            for item in segments
        ],
        "scenes": scene.get("scenes", [])[:120],
        "requirements": "Pick 3-12 semantic highlights. Start/end must stay inside provided candidate clip or transcript timestamps.",
    }, ensure_ascii=False)


def _metadata_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["titles", "descriptions", "tags", "hashtags", "cover_titles", "platform_notes"],
        "properties": {
            "titles": {"type": "array", "items": {"type": "string"}},
            "descriptions": {"type": "array", "items": {"type": "string"}},
            "tags": {"type": "array", "items": {"type": "string"}},
            "hashtags": {"type": "array", "items": {"type": "string"}},
            "cover_titles": {"type": "array", "items": {"type": "string"}},
            "platform_notes": {"type": "array", "items": {"type": "string"}},
        },
    }


def _highlights_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "highlights"],
        "properties": {
            "summary": {"type": "string"},
            "highlights": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["start", "end", "score", "reason", "recommended_use"],
                    "properties": {
                        "start": {"type": "number"},
                        "end": {"type": "number"},
                        "score": {"type": "number"},
                        "reason": {"type": "string"},
                        "recommended_use": {"type": "string"},
                    },
                },
            },
        },
    }


def _attach_highlights_to_cuts(job_dir: Path, highlights: dict[str, Any]) -> None:
    cuts_path = job_dir / "cuts.json"
    cuts = read_json_file(cuts_path)
    if not cuts:
        return
    semantic = highlights.get("highlights", [])
    if not isinstance(semantic, list):
        return
    clips = []
    for clip in cuts.get("clips", []):
        value = dict(clip)
        value["semantic_score"] = _semantic_score_for_clip(value, semantic)
        clips.append(value)
    cuts["clips"] = clips
    cuts["semantic_highlights"] = semantic
    write_json_atomic(cuts_path, cuts)


def _semantic_score_for_clip(clip: dict[str, Any], highlights: list[Any]) -> float:
    try:
        start = float(clip.get("start") or 0)
        end = float(clip.get("end") or 0)
    except (TypeError, ValueError):
        return 0.0
    best = 0.0
    for item in highlights:
        if not isinstance(item, dict):
            continue
        try:
            item_start = float(item.get("start") or 0)
            item_end = float(item.get("end") or 0)
            score = float(item.get("score") or 0)
        except (TypeError, ValueError):
            continue
        if item_start < end and item_end > start:
            best = max(best, score)
    return round(best, 1)
