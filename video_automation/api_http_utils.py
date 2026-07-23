from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qs


RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")


def parse_range(range_header: str | None, size: int) -> tuple[int, int] | None:
    if not range_header or size <= 0:
        return None
    match = RANGE_RE.match(range_header.strip())
    if not match:
        return None
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        return None
    if start_text:
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
    else:
        suffix_length = int(end_text)
        if suffix_length <= 0:
            return None
        start = max(0, size - suffix_length)
        end = size - 1
    if start < 0 or end < start or start >= size:
        return None
    return start, min(end, size - 1)


def event_last_id(header_value: str | None, query: str) -> int:
    candidates = [header_value]
    candidates.extend(parse_qs(query).get("last_id", []))
    for value in candidates:
        if value is None:
            continue
        try:
            return max(0, int(str(value).strip()))
        except ValueError:
            continue
    return 0


def format_sse(event_type: str, payload: dict[str, Any], *, event_id: int) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    lines = [f"id: {event_id}", f"event: {event_type}"]
    lines.extend(f"data: {line}" for line in data.splitlines() or ["{}"])
    return "\n".join(lines) + "\n\n"
