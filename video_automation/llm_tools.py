from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .io_utils import read_json_file, write_json_atomic
from .llm_output import (
    StructuredOutputError,
    parse_structured_json,
    validate_required_shape,
)
from .provider_errors import (
    ProviderRequestError,
    provider_configuration_error,
    provider_error_code,
    provider_http_error,
    provider_network_error,
)


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
ALLOWED_METADATA_KEYS = {"titles", "descriptions", "tags", "hashtags", "cover_titles", "platform_notes"}
HIGHLIGHT_MIN_SECONDS = 3.0
HIGHLIGHT_MAX_SECONDS = 60.0
HIGHLIGHT_MAX_COUNT = 12
LOCAL_HIGHLIGHT_GLOBAL_MAX_SPANS = 12
HIGHLIGHT_SYSTEM_PROMPT = (
    "You are a senior short-video editor analyzing livestream recordings. "
    "The transcript may be Korean, Chinese, English, or mixed. Understand the original meaning, "
    "but write the summary, reasons, and recommended uses in concise Simplified Chinese. "
    "Select self-contained moments with a clear hook and payoff: surprise, conflict, reveal, "
    "challenge success or failure, comedy, strong opinion, or an emotional shift. "
    "Reject greetings, subscription thanks, routine notices, repetitive filler, contextless fragments, "
    "silence, and music-only passages. Use only supplied timestamps and cite concrete transcript evidence."
)


def generate_metadata(settings: Settings, job_dir: Path, *, platform: str = "douyin", force: bool = False) -> dict[str, Any]:
    output_path = job_dir / "metadata.json"
    if output_path.exists() and not force:
        cached = read_json_file(output_path)
        if cached is not None:
            return cached
    backend = settings.llm_provider
    try:
        payload = _call_structured_llm(
            settings,
            system="You are a Chinese short-video publishing assistant. Return concise, platform-ready metadata.",
            user=_metadata_prompt(job_dir, platform),
            schema=_metadata_schema(),
            schema_name="video_metadata",
        )
    except Exception as exc:
        if not getattr(settings, "metadata_fallback_heuristic", True):
            raise
        payload = _heuristic_metadata_payload(job_dir, exc)
        backend = "heuristic_fallback"
    payload.update({
        "status": "ready",
        "backend": backend,
        "model": settings.llm_model if backend != "heuristic_fallback" else "",
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


def _heuristic_metadata_payload(job_dir: Path, error: Exception) -> dict[str, Any]:
    """Rule-based metadata used when every LLM provider is unavailable."""
    highlights = read_json_file(job_dir / "highlights.json") or {}
    cuts = read_json_file(job_dir / "cuts.json") or {}
    transcript = read_json_file(job_dir / "transcript.json") or {}

    reasons = [
        str(item.get("reason") or "").strip()
        for item in (highlights.get("highlights") or [])
        if isinstance(item, dict) and str(item.get("reason") or "").strip()
    ][:6]
    summary = str(highlights.get("summary") or "").strip()
    if not reasons and summary:
        reasons = [summary]

    top_clips = sorted(
        [clip for clip in (cuts.get("clips") or []) if isinstance(clip, dict)],
        key=lambda clip: float(clip.get("final_score") or clip.get("content_score") or 0),
        reverse=True,
    )[:5]
    clip_texts = [
        str(clip.get("text") or "").strip()
        for clip in top_clips
        if str(clip.get("text") or "").strip()
    ]

    titles: list[str] = []
    if summary:
        titles.append(_compact_title(summary, limit=24))
    for reason in reasons[:2]:
        title = _compact_title(reason, limit=20)
        if title and title not in titles:
            titles.append(title)
    if not titles and clip_texts:
        titles.append(_compact_title(clip_texts[0], limit=20))
    if not titles:
        titles.append("精彩片段回顾")

    descriptions = (reasons or clip_texts or ["本期精选片段，欢迎观看。"])[:3]
    keywords = _frequent_keywords(" ".join([summary] + reasons + clip_texts))
    tags = keywords[:8] if keywords else ["短视频", "精彩片段"]
    hashtags = [f"#{tag.replace(' ', '')}" for tag in keywords[:4]] or ["#短视频", "#精彩片段"]

    return {
        "titles": titles[:3],
        "descriptions": [item[:120] for item in descriptions],
        "tags": tags,
        "hashtags": hashtags,
        "cover_titles": titles[:2],
        "platform_notes": [
            "由本地规则生成的兜底文案：AI 供应商不可用，建议发布前人工润色。",
            f"fallback_reason: {provider_error_code(error)}",
        ],
        "generator": "heuristic_fallback",
        "fallback_reason": provider_error_code(error),
    }


_TITLE_STRIP_CHARS = "，。！？；：、,.!?;: \t\"'“”‘’()（）[]【】\n"


def _compact_title(text: str, *, limit: int) -> str:
    cleaned = str(text or "").strip().strip(_TITLE_STRIP_CHARS)
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit].rstrip(_TITLE_STRIP_CHARS)


_KEYWORD_STOP_CHARS = set("的了是在和与及或有也不这那你我他她它们吧啊吗呢呀哦嗯")


def _frequent_keywords(text: str, *, limit: int = 12) -> list[str]:
    """Rank CJK 2-6 char chunks and ASCII words by frequency."""
    import re
    from collections import Counter

    chunks: list[str] = []
    for token in re.findall(r"[\u4e00-\u9fff]{2,6}|[A-Za-z][A-Za-z0-9_-]{1,15}", str(text or "")):
        if any(char in _KEYWORD_STOP_CHARS for char in token[:2]):
            continue
        chunks.append(token)
    if not chunks:
        return []
    counts = Counter(chunks)
    ranked = sorted(counts, key=lambda token: (-counts[token], len(token), token))
    repeated = [token for token in ranked if counts[token] >= 2]
    singles = [token for token in ranked if counts[token] == 1]
    return (repeated + singles)[:limit]


def generate_highlights(settings: Settings, job_dir: Path, *, force: bool = False) -> dict[str, Any]:
    output_path = job_dir / "highlights.json"
    if output_path.exists() and not force:
        cached = read_json_file(output_path)
        if cached is not None:
            return cached
    attempt_path = job_dir / "highlights_attempt.json"
    started_at = datetime.now().isoformat(timespec="seconds")
    attempt = {
        "status": "running",
        "provider": settings.llm_provider,
        "fallback_provider": getattr(settings, "llm_fallback_provider", "") or "",
        "model": settings.llm_model,
        "started_at": started_at,
        "completed_at": "",
        "error_code": "",
        "error": "",
    }
    write_json_atomic(attempt_path, attempt)
    try:
        payload = analyze_highlights(settings, job_dir)
    except Exception as exc:
        write_json_atomic(attempt_path, {
            **attempt,
            "status": "failed",
            "completed_at": datetime.now().isoformat(timespec="seconds"),
            "error_code": provider_error_code(exc),
            "error": str(exc),
        })
        raise
    payload.update({
        "status": "ready",
        "backend": settings.llm_provider,
        "model": settings.llm_model,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    })
    write_json_atomic(output_path, payload)
    _attach_highlights_to_cuts(job_dir, payload)
    write_json_atomic(attempt_path, {
        **attempt,
        "status": "done",
        "completed_at": datetime.now().isoformat(timespec="seconds"),
        "highlight_count": len(payload.get("highlights") or []),
    })
    return payload


def call_structured_llm(settings: Settings, *, system: str, user: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
    return _call_structured_llm(settings, system=system, user=user, schema=schema, schema_name=schema_name)


def analyze_highlights(settings: Settings, job_dir: Path) -> dict[str, Any]:
    context = _highlights_context(job_dir)
    global_transcript_spans = _global_highlight_prompt_spans(context)
    if (
        settings.llm_provider.strip().lower() == "local"
        and len(global_transcript_spans) > LOCAL_HIGHLIGHT_GLOBAL_MAX_SPANS
    ):
        payload = _call_local_chunked_highlights(
            settings,
            job_dir,
            context,
            _highlight_prompt_spans(job_dir, context),
        )
    else:
        payload = _call_structured_llm(
            settings,
            system=HIGHLIGHT_SYSTEM_PROMPT,
            user=_highlights_prompt_from_context(context, global_transcript_spans),
            schema=_highlights_schema(),
            schema_name="semantic_highlights",
        )
    return _normalize_highlights_payload(job_dir, payload)


def _call_structured_llm(settings: Settings, *, system: str, user: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
    """Call the configured LLM provider with schema validation and repair retries.

    Malformed or schema-invalid output is retried with a corrective prompt.
    If every attempt on the primary provider fails (or the provider itself
    errors) and LLM_FALLBACK_PROVIDER is configured, the whole sequence is
    retried on the fallback before giving up.
    """
    chain = _structured_llm_provider_chain(settings)
    if not chain:
        raise provider_configuration_error(
            settings.llm_provider or "LLM",
            "structured request",
            "provider_unsupported",
            "LLM_PROVIDER is not configured.",
        )
    attempts = 1 + max(0, int(getattr(settings, "llm_max_repair_retries", 2)))
    last_error: BaseException | None = None
    for index, provider in enumerate(chain):
        prompt = user
        try:
            for _ in range(attempts):
                try:
                    return _structured_attempt(
                        settings,
                        provider,
                        system=system,
                        prompt=prompt,
                        schema=schema,
                        schema_name=schema_name,
                    )
                except StructuredOutputError as exc:
                    last_error = exc
                    prompt = _repair_prompt(user, exc)
        except Exception as exc:  # provider-level failure: try the fallback provider
            last_error = exc
        if index == len(chain) - 1:
            if isinstance(last_error, StructuredOutputError):
                raise ProviderRequestError(
                    provider,
                    "structured request",
                    "response_invalid",
                    str(last_error),
                ) from last_error
            assert last_error is not None
            raise last_error
    raise last_error  # pragma: no cover - chain is never empty here


def _structured_llm_provider_chain(settings: Settings) -> list[str]:
    primary = settings.llm_provider.strip().lower()
    chain = [primary] if primary else []
    fallback = str(getattr(settings, "llm_fallback_provider", "") or "").strip().lower()
    if fallback and fallback not in chain:
        chain.append(fallback)
    return chain


def _repair_prompt(user: str, error: StructuredOutputError) -> str:
    return (
        f"{user}\n\n"
        f"Your previous response was rejected: {str(error)[:400]}\n"
        "Respond again with ONLY a corrected JSON object matching the required schema."
    )


def _structured_attempt(
    settings: Settings,
    provider: str,
    *,
    system: str,
    prompt: str,
    schema: dict[str, Any],
    schema_name: str,
) -> dict[str, Any]:
    """One provider attempt; repairable output problems raise StructuredOutputError."""
    if provider == "local":
        from .local_ai import call_local_structured_llm

        try:
            return call_local_structured_llm(
                settings,
                system=system,
                user=prompt,
                schema=schema,
                schema_name=schema_name,
            )
        except ProviderRequestError as exc:
            if exc.code == "response_invalid":
                raise StructuredOutputError(str(exc)) from exc
            raise
    if provider == "google":
        text = _request_google_text(settings, system=system, user=prompt, schema=schema)
    elif provider == "openai":
        text = _request_openai_text(
            settings, system=system, user=prompt, schema=schema, schema_name=schema_name
        )
    else:
        raise provider_configuration_error(
            provider or "LLM",
            "structured request",
            "provider_unsupported",
            f"Unsupported LLM_PROVIDER: {provider}",
        )
    parsed = parse_structured_json(text, provider=provider)
    validate_required_shape(parsed, schema)
    return parsed


def _request_openai_text(
    settings: Settings,
    *,
    system: str,
    user: str,
    schema: dict[str, Any],
    schema_name: str,
) -> str:
    if not settings.openai_api_key.strip():
        raise provider_configuration_error(
            "OpenAI",
            "structured request",
            "credentials_missing",
            "OPENAI_API_KEY is not configured.",
        )
    if not settings.llm_model.strip():
        raise provider_configuration_error(
            "OpenAI",
            "structured request",
            "model_missing",
            "LLM_MODEL is not configured.",
        )
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
        raise provider_http_error(
            "OpenAI",
            "structured request",
            exc.code,
            detail,
        ) from exc
    except OSError as exc:
        raise provider_network_error("OpenAI", "structured request", exc) from exc
    text = _extract_output_text(raw)
    if not text.strip():
        raise StructuredOutputError("OpenAI returned an empty response.")
    return text


def _request_google_text(
    settings: Settings,
    *,
    system: str,
    user: str,
    schema: dict[str, Any],
) -> str:
    if not settings.google_api_key.strip():
        raise provider_configuration_error(
            "Google Gemini",
            "structured request",
            "credentials_missing",
            "GOOGLE_API_KEY is not configured.",
        )
    if not settings.llm_model.strip():
        raise provider_configuration_error(
            "Google Gemini",
            "structured request",
            "model_missing",
            "LLM_MODEL is not configured.",
        )
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
        raise provider_http_error(
            "Google Gemini",
            "structured request",
            exc.code,
            detail,
        ) from exc
    except OSError as exc:
        raise provider_network_error("Google Gemini", "structured request", exc) from exc
    return _extract_google_text(raw)


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
    context = _highlights_context(job_dir)
    return _highlights_prompt_from_context(
        context,
        _global_highlight_prompt_spans(context),
    )


def _highlights_prompt_from_context(
    context: dict[str, Any],
    transcript_spans: list[dict[str, Any]],
    *,
    target_count: int | None = None,
    chunk_label: str = "",
) -> str:
    normalized_target = min(
        max(0, target_count if target_count is not None else 4),
        len(transcript_spans),
    )
    return json.dumps({
        "source_name": context.get("source_name"),
        "duration_seconds": context.get("duration_seconds"),
        "transcript_language": context.get("transcript_language"),
        "selection_task": {
            "target_highlight_count": normalized_target,
            "primary_evidence": "transcript_spans",
            "coverage_chunk": chunk_label,
            "instruction": (
                "Read every transcript span from beginning to end, including late sections. "
                "Rank moments by the concrete event and reaction in the transcript, not by "
                "speech density, silence boundaries, scene changes, or generic emotional wording."
            ),
        },
        "transcript_spans": transcript_spans,
        "requirements": [
            (
                f"Return exactly {normalized_target} distinct highlights when at least "
                f"{normalized_target} transcript spans contain meaningful speech."
            ),
            "Prefer a complete setup and payoff over a generic topic summary.",
            "Prefer precise 8-45 second intervals and never exceed 60 seconds.",
            (
                "Each interval must stay within one supplied transcript span. Copy that span's "
                "start/end timestamps when it is at most 60 seconds; otherwise choose a precise "
                "8-45 second sub-interval inside it."
            ),
            "Give the strongest moments scores in the 80-100 range and rank them descending.",
            "Explain the specific action, quote, misunderstanding, reversal, or reaction.",
            "Reject greetings, subscription thanks, routine notices, filler, silence, and music-only passages.",
            "Refer to the on-screen speaker as 主播, not 用户 or 观众, unless the transcript clearly quotes viewers.",
            "Write summary, reason, and recommended_use in concise Simplified Chinese.",
        ],
    }, ensure_ascii=False)


def _highlights_context(job_dir: Path) -> dict[str, Any]:
    manifest = read_json_file(job_dir / "manifest.json") or {}
    transcript = read_json_file(job_dir / "transcript.json") or {}
    cuts = read_json_file(job_dir / "cuts.json") or {}
    scene = read_json_file(job_dir / "scene.json") or {}
    raw_clips = cuts.get("clips") if isinstance(cuts.get("clips"), list) else []
    clips = sorted(
        [item for item in raw_clips if isinstance(item, dict)],
        key=lambda item: _finite_number(item.get("content_score"), default=0.0),
        reverse=True,
    )[:30]
    raw_segments = transcript.get("segments") if isinstance(transcript.get("segments"), list) else []
    segments = [item for item in raw_segments if isinstance(item, dict)]
    sampled_segments = _sample_evenly(segments, 180)
    raw_scenes = scene.get("scenes") if isinstance(scene.get("scenes"), list) else []
    sampled_scenes = _sample_evenly(raw_scenes, 120)
    candidate_clips = []
    for clip in clips:
        start = _finite_number(clip.get("start"))
        end = _finite_number(clip.get("end"))
        if start is None or end is None or end <= start:
            continue
        clip_text = str(clip.get("subtitle_text") or clip.get("transcript_text") or "").strip()
        context_text = _transcript_text_for_interval(segments, start, end, padding=1.5, max_chars=700)
        candidate_clips.append({
            "start": start,
            "end": end,
            "duration": round(end - start, 3),
            "structure_score": _finite_number(clip.get("content_score"), default=0.0),
            "scene_count": clip.get("scene_count"),
            "text": (clip_text or context_text)[:700],
            "structural_reason": str(clip.get("reason") or "")[:240],
        })
    source_name = manifest.get("source_name") or manifest.get("source_path") or job_dir.name
    return {
        "source_name": source_name,
        "duration_seconds": (
            _finite_number(manifest.get("duration_seconds"))
            or _finite_number(cuts.get("duration_seconds"))
        ),
        "transcript_language": transcript.get("language") or transcript.get("detected_language") or "",
        "candidate_clips": [
            clip for clip in candidate_clips
        ],
        "transcript_sample": [
            {"start": item.get("start"), "end": item.get("end"), "text": item.get("text")}
            for item in sampled_segments
        ],
        "scenes": sampled_scenes,
        "requirements": [
            "Return 3-12 highlights when the content supports them; returning fewer is better than inventing weak moments.",
            "Prefer precise 8-45 second intervals with enough setup to understand the payoff.",
            "Each start/end interval must be contained in a supplied candidate clip or covered transcript span.",
            "Score 0-100 for short-video value, not merely speech density or scene changes.",
            "Explain the specific event or line that makes the moment worth watching.",
            "Do not select greetings, subscription thanks, routine notices, repeated filler, silence, or music-only passages.",
            "Write summary, reason, and recommended_use in Simplified Chinese.",
        ],
    }


def _highlight_prompt_spans(
    job_dir: Path,
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    sampled = context.get("transcript_sample")
    if not isinstance(sampled, list):
        return []
    transcript = read_json_file(job_dir / "transcript.json") or {}
    raw_segments = transcript.get("segments")
    if not isinstance(raw_segments, list):
        raw_segments = []
    segment_lookup: dict[tuple[float, float], dict[str, Any]] = {}
    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        start = _finite_number(item.get("start"))
        end = _finite_number(item.get("end"))
        if start is None or end is None:
            continue
        segment_lookup[(round(start, 3), round(end, 3))] = item

    bounded: list[dict[str, Any]] = []
    for item in sampled:
        if not isinstance(item, dict):
            continue
        start = _finite_number(item.get("start"))
        end = _finite_number(item.get("end"))
        text = str(item.get("text") or "").strip()
        if start is None or end is None or end <= start or not text:
            continue
        normalized = {
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
        }
        if end - start <= HIGHLIGHT_MAX_SECONDS:
            bounded.append(normalized)
            continue
        source = segment_lookup.get((round(start, 3), round(end, 3)), {})
        bounded.extend(_split_highlight_prompt_span(normalized, source.get("words")))
    return _sample_evenly(bounded, 240)


def _global_highlight_prompt_spans(
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    sampled = context.get("transcript_sample")
    if not isinstance(sampled, list):
        return []
    spans: list[dict[str, Any]] = []
    for item in sampled:
        if not isinstance(item, dict):
            continue
        start = _finite_number(item.get("start"))
        end = _finite_number(item.get("end"))
        text = str(item.get("text") or "").strip()
        if start is None or end is None or end <= start or not text:
            continue
        spans.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
        })
    return spans


def _split_highlight_prompt_span(
    span: dict[str, Any],
    raw_words: Any,
    *,
    window_seconds: float = 50.0,
    stride_seconds: float = 40.0,
) -> list[dict[str, Any]]:
    start = float(span["start"])
    end = float(span["end"])
    words = [item for item in raw_words if isinstance(item, dict)] if isinstance(raw_words, list) else []
    windows: list[dict[str, Any]] = []
    window_start = start
    while window_start < end:
        window_end = min(end, window_start + window_seconds)
        selected_words = []
        for word in words:
            word_start = _finite_number(word.get("start"))
            word_end = _finite_number(word.get("end"))
            if word_start is None or word_end is None:
                continue
            if word_start < window_end and word_end > window_start:
                value = str(word.get("word") or "").strip()
                if value:
                    selected_words.append(value)
        text = " ".join(selected_words).strip()
        if not text:
            source_text = str(span.get("text") or "")
            relative_start = (window_start - start) / max(end - start, 0.001)
            relative_end = (window_end - start) / max(end - start, 0.001)
            left = int(len(source_text) * relative_start)
            right = max(left + 1, int(len(source_text) * relative_end))
            text = source_text[left:right].strip()
        if text:
            windows.append({
                "start": round(window_start, 3),
                "end": round(window_end, 3),
                "text": text,
            })
        if window_end >= end:
            break
        window_start += stride_seconds
    return windows


def _chunk_transcript_spans(
    spans: list[dict[str, Any]],
    *,
    max_spans: int = 8,
    max_chars: int = 4500,
) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for span in spans:
        span_chars = len(json.dumps(span, ensure_ascii=False))
        if current and (
            len(current) >= max_spans
            or current_chars + span_chars > max_chars
        ):
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(span)
        current_chars += span_chars
    if current:
        chunks.append(current)
    return chunks


def _call_local_chunked_highlights(
    settings: Settings,
    job_dir: Path,
    context: dict[str, Any],
    transcript_spans: list[dict[str, Any]],
) -> dict[str, Any]:
    chunks = _chunk_transcript_spans(transcript_spans)
    candidates: list[dict[str, Any]] = []
    summaries: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        payload = _call_structured_llm(
            settings,
            system=HIGHLIGHT_SYSTEM_PROMPT,
            user=_highlights_prompt_from_context(
                context,
                chunk,
                target_count=min(2, len(chunk)),
                chunk_label=f"{index}/{len(chunks)}",
            ),
            schema=_highlights_schema(),
            schema_name=f"semantic_highlights_chunk_{index}",
        )
        normalized = _normalize_highlights_payload(job_dir, payload)
        candidates.extend(normalized.get("highlights") or [])
        summary = _clean_model_text(normalized.get("summary"), max_chars=240)
        if summary:
            summaries.append(summary)

    merged = _normalize_highlights_payload(
        job_dir,
        {
            "summary": "；".join(summaries)[:600],
            "highlights": candidates,
        },
    )
    ranked = merged.get("highlights")
    if not isinstance(ranked, list) or not ranked:
        return merged
    return merged


def _normalize_highlights_payload(job_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    raw_highlights = payload.get("highlights")
    if not isinstance(raw_highlights, list):
        raw_highlights = []
    allowed_ranges = _highlight_allowed_ranges(job_dir)
    duration_limit = _highlight_duration_limit(job_dir, allowed_ranges)
    normalized: list[dict[str, Any]] = []
    for raw in raw_highlights:
        if not isinstance(raw, dict):
            continue
        start = _finite_number(raw.get("start"))
        end = _finite_number(raw.get("end"))
        score = _finite_number(raw.get("score"))
        if start is None or end is None or score is None:
            continue
        start = max(0.0, start)
        if duration_limit is not None:
            end = min(end, duration_limit)
        fitted = _fit_highlight_interval(start, end, allowed_ranges)
        if fitted is None:
            continue
        start, end = fitted
        duration = end - start
        if duration < HIGHLIGHT_MIN_SECONDS or duration > HIGHLIGHT_MAX_SECONDS:
            continue
        reason = _clean_model_text(raw.get("reason"), max_chars=320)
        if not reason:
            continue
        recommended_use = _clean_model_text(raw.get("recommended_use"), max_chars=180)
        normalized.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "score": round(max(0.0, min(100.0, score)), 1),
            "reason": reason,
            "recommended_use": recommended_use or "短视频高光片段",
        })

    ranked = sorted(
        normalized,
        key=lambda item: (
            float(item["score"]),
            -(float(item["end"]) - float(item["start"])),
        ),
        reverse=True,
    )
    deduplicated: list[dict[str, Any]] = []
    for item in ranked:
        if any(_highlight_duplicate(item, existing) for existing in deduplicated):
            continue
        deduplicated.append(item)
        if len(deduplicated) >= HIGHLIGHT_MAX_COUNT:
            break

    summary = _clean_model_text(payload.get("summary"), max_chars=600)
    if not summary and deduplicated:
        summary = "；".join(item["reason"] for item in deduplicated[:3])[:600]
    return {"summary": summary, "highlights": deduplicated}


def _highlight_allowed_ranges(job_dir: Path) -> list[tuple[float, float]]:
    transcript = read_json_file(job_dir / "transcript.json") or {}
    cuts = read_json_file(job_dir / "cuts.json") or {}
    ranges: list[tuple[float, float]] = []
    raw_clips = cuts.get("clips") if isinstance(cuts.get("clips"), list) else []
    clips = sorted(
        [item for item in raw_clips if isinstance(item, dict)],
        key=lambda item: _finite_number(item.get("content_score"), default=0.0),
        reverse=True,
    )[:30]
    for clip in clips:
        interval = _valid_interval(clip.get("start"), clip.get("end"))
        if interval is not None:
            ranges.append(interval)
    raw_segments = transcript.get("segments") if isinstance(transcript.get("segments"), list) else []
    transcript_ranges = [
        interval
        for item in raw_segments
        if isinstance(item, dict)
        for interval in [_valid_interval(item.get("start"), item.get("end"))]
        if interval is not None
    ]
    ranges.extend(_merge_ranges(transcript_ranges, max_gap=1.5))
    return _merge_ranges(ranges, max_gap=0.0)


def _highlight_duration_limit(job_dir: Path, ranges: list[tuple[float, float]]) -> float | None:
    manifest = read_json_file(job_dir / "manifest.json") or {}
    cuts = read_json_file(job_dir / "cuts.json") or {}
    values = [
        _finite_number(manifest.get("duration_seconds")),
        _finite_number(cuts.get("duration_seconds")),
        max((end for _, end in ranges), default=None),
    ]
    finite = [value for value in values if value is not None and value > 0]
    return max(finite) if finite else None


def _fit_highlight_interval(
    start: float,
    end: float,
    allowed_ranges: list[tuple[float, float]],
) -> tuple[float, float] | None:
    if not math.isfinite(start) or not math.isfinite(end) or end <= start:
        return None
    if not allowed_ranges:
        return start, end
    tolerance = 0.75
    matches = [
        (range_start, range_end)
        for range_start, range_end in allowed_ranges
        if start >= range_start - tolerance and end <= range_end + tolerance
    ]
    if not matches:
        return None
    range_start, range_end = min(matches, key=lambda item: item[1] - item[0])
    fitted_start = max(start, range_start)
    fitted_end = min(end, range_end)
    return (fitted_start, fitted_end) if fitted_end > fitted_start else None


def _highlight_duplicate(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_start = float(left["start"])
    left_end = float(left["end"])
    right_start = float(right["start"])
    right_end = float(right["end"])
    overlap = max(0.0, min(left_end, right_end) - max(left_start, right_start))
    shorter = min(left_end - left_start, right_end - right_start)
    return shorter > 0 and overlap / shorter >= 0.7


def _valid_interval(start_value: Any, end_value: Any) -> tuple[float, float] | None:
    start = _finite_number(start_value)
    end = _finite_number(end_value)
    if start is None or end is None or end <= start:
        return None
    return max(0.0, start), end


def _merge_ranges(ranges: list[tuple[float, float]], *, max_gap: float) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for start, end in sorted(ranges):
        if not merged or start > merged[-1][1] + max_gap:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def _sample_evenly(items: list[Any], limit: int) -> list[Any]:
    if limit <= 0 or not items:
        return []
    if len(items) <= limit:
        return list(items)
    if limit == 1:
        return [items[0]]
    return [
        items[round(index * (len(items) - 1) / (limit - 1))]
        for index in range(limit)
    ]


def _transcript_text_for_interval(
    segments: list[dict[str, Any]],
    start: float,
    end: float,
    *,
    padding: float = 0.0,
    max_chars: int = 600,
) -> str:
    texts = []
    for segment in segments:
        segment_start = _finite_number(segment.get("start"))
        segment_end = _finite_number(segment.get("end"))
        if segment_start is None or segment_end is None:
            continue
        if segment_start < end + padding and segment_end > start - padding:
            text = str(segment.get("text") or "").strip()
            if text:
                texts.append(text)
    return " ".join(texts)[:max_chars]


def _clean_model_text(value: Any, *, max_chars: int) -> str:
    return " ".join(str(value or "").split())[:max_chars]


def _finite_number(value: Any, *, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


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
                "maxItems": HIGHLIGHT_MAX_COUNT,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["start", "end", "score", "reason", "recommended_use"],
                    "properties": {
                        "start": {"type": "number", "minimum": 0},
                        "end": {"type": "number", "minimum": 0},
                        "score": {"type": "number", "minimum": 0, "maximum": 100},
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
