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


def call_structured_llm(settings: Settings, *, system: str, user: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
    return _call_structured_llm(settings, system=system, user=user, schema=schema, schema_name=schema_name)


def _call_structured_llm(settings: Settings, *, system: str, user: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
    provider = settings.llm_provider.strip().lower()
    if provider == "google":
        return _call_google_structured_llm(settings, system=system, user=user, schema=schema)
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


def _call_google_structured_llm(
    settings: Settings,
    *,
    system: str,
    user: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    if not settings.google_api_key.strip():
        raise RuntimeError("GOOGLE_API_KEY is not configured")
    if not settings.llm_model.strip():
        raise RuntimeError("LLM_MODEL is not configured")
    request_payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
        },
    }
    request = urllib.request.Request(
        _google_model_url(settings.google_base_url, settings.llm_model),
        data=json.dumps(request_payload).encode("utf-8"),
        headers={
            "x-goog-api-key": settings.google_api_key.strip(),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Google Gemini request failed: {exc.code} {detail}") from exc
    except OSError as exc:
        raise RuntimeError(f"Google Gemini request failed: {exc}") from exc
    text = _extract_google_text(raw)
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise RuntimeError("Google Gemini returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Google Gemini returned a non-object JSON payload")
    return parsed


def _extract_google_text(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for candidate in payload.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        if not isinstance(content, dict):
            continue
        for part in content.get("parts", []):
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
    text = "\n".join(parts).strip()
    if not text:
        raise RuntimeError("Google Gemini response did not include text")
    return text


def _google_model_url(base_url: str, model: str) -> str:
    base = (base_url or "https://generativelanguage.googleapis.com/v1beta").strip().rstrip("/")
    model_name = model.strip()
    if model_name.startswith("models/"):
        model_name = model_name.removeprefix("models/")
    return f"{base}/models/{model_name}:generateContent"


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
        matches = _semantic_matches_for_clip(value, semantic)
        semantic_score = max((float(item.get("score") or 0.0) for item in matches), default=0.0)
        structure_score = float(value.get("content_score") or 0.0)
        value["semantic_score"] = round(semantic_score, 1)
        value["semantic_reasons"] = [str(item.get("reason") or "").strip() for item in matches if str(item.get("reason") or "").strip()][:3]
        value["semantic_recommended_use"] = [str(item.get("recommended_use") or "").strip() for item in matches if str(item.get("recommended_use") or "").strip()][:3]
        value["final_score"] = round(structure_score * 0.4 + semantic_score * 0.6, 1)
        value["recommendation"] = "strong_keep" if value["final_score"] >= 70 else "review" if value["final_score"] >= 42 else "trim_candidate"
        clips.append(value)
    ranked = sorted(clips, key=lambda item: float(item.get("final_score") or 0), reverse=True)
    ranks = {id(item): index for index, item in enumerate(ranked, start=1)}
    for item in clips:
        item["final_rank"] = ranks[id(item)]
    cuts["clips"] = clips
    cuts["semantic_highlights"] = semantic
    cuts["content_scoring"] = {
        "method": "0.4*structure_score+0.6*semantic_score",
        "note": "Final scores rank clips for review; they do not auto-delete or reorder media.",
    }
    write_json_atomic(cuts_path, cuts)


def _semantic_score_for_clip(clip: dict[str, Any], highlights: list[Any]) -> float:
    matches = _semantic_matches_for_clip(clip, highlights)
    return round(max((float(item.get("score") or 0.0) for item in matches), default=0.0), 1)


def _semantic_matches_for_clip(clip: dict[str, Any], highlights: list[Any]) -> list[dict[str, Any]]:
    try:
        start = float(clip.get("start") or 0)
        end = float(clip.get("end") or 0)
    except (TypeError, ValueError):
        return []
    matches = []
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
            value = dict(item)
            value["score"] = max(0.0, min(100.0, score))
            matches.append(value)
    return sorted(matches, key=lambda item: float(item.get("score") or 0.0), reverse=True)
