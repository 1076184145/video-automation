from __future__ import annotations

from typing import Any, Iterable


def apply_replacements(text: str, replacements: Iterable[tuple[str, str]]) -> str:
    value = text
    for source, target in sorted((item for item in replacements if item[0]), key=lambda item: len(item[0]), reverse=True):
        value = value.replace(source, target)
    return value


def censor_text(text: str, words: Iterable[str], *, replacement: str = "[哔]") -> str:
    value = text
    for word in sorted((item for item in words if item), key=len, reverse=True):
        value = value.replace(word, replacement)
    return value


def censor_transcript_payload(payload: dict[str, Any], words: Iterable[str], *, replacement: str = "[哔]") -> dict[str, Any]:
    censored = dict(payload)
    if isinstance(censored.get("text"), str):
        censored["text"] = censor_text(censored["text"], words, replacement=replacement)

    segments = censored.get("segments")
    if isinstance(segments, list):
        censored_segments = []
        for segment in segments:
            if not isinstance(segment, dict):
                censored_segments.append(segment)
                continue
            value = dict(segment)
            if isinstance(value.get("text"), str):
                value["text"] = censor_text(value["text"], words, replacement=replacement)
            censored_segments.append(value)
        censored["segments"] = censored_segments
    return censored
