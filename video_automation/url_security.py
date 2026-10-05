from __future__ import annotations

from urllib.parse import urlsplit


def require_http_url(url: str) -> str:
    """Validate a configured provider URL before passing it to urllib."""
    value = str(url or "").strip()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("provider URL must use http or https with a valid host") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or any(ord(char) < 32 for char in value)
        or port == 0
    ):
        raise ValueError("provider URL must use http or https with a valid host")
    return value
