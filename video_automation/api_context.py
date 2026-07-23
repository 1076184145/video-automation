from __future__ import annotations

import threading
from urllib.parse import urlparse

from .config import Settings
from .library_api import queue_repository_for
from .task_queue import QueueRepository


class APIContext:
    """Thread-safe mutable state shared by otherwise stateless HTTP handlers."""

    def __init__(self, settings: Settings):
        self._lock = threading.RLock()
        self._settings = settings
        self._allowed_origins = allowed_api_origins(settings)
        self.queue_repository: QueueRepository = queue_repository_for(settings)

    @property
    def settings(self) -> Settings:
        with self._lock:
            return self._settings

    def replace_settings(self, settings: Settings) -> None:
        with self._lock:
            self._settings = settings
            self._allowed_origins = allowed_api_origins(settings)

    def origin_is_allowed(self, origin: str | None) -> bool:
        normalized = normalize_origin(origin)
        if normalized is None:
            return True
        with self._lock:
            return normalized in self._allowed_origins

    def cors_origin(self, origin: str | None) -> str | None:
        normalized = normalize_origin(origin)
        if not normalized:
            return None
        with self._lock:
            return normalized if normalized in self._allowed_origins else None


def normalize_origin(origin: str | None) -> str | None:
    if origin is None:
        return None
    value = origin.strip().rstrip("/")
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def allowed_api_origins(settings: Settings) -> set[str]:
    origins = default_api_origins(settings)
    for raw_origin in settings.api_allowed_origins:
        origin = normalize_origin(raw_origin)
        if origin:
            origins.add(origin)
    return origins


def default_api_origins(settings: Settings) -> set[str]:
    port = settings.api_port
    origins = {
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        f"http://[::1]:{port}",
    }
    host = settings.api_host.strip()
    if host and host not in {"0.0.0.0", "::", "[::]"}:
        origin_host = host
        if ":" in origin_host and not origin_host.startswith("["):
            origin_host = f"[{origin_host}]"
        origins.add(f"http://{origin_host.lower()}:{port}")
    return origins
