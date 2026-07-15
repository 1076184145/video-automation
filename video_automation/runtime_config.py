from __future__ import annotations

import hashlib
import json
from dataclasses import fields, is_dataclass, replace
from pathlib import Path
from typing import Any

from .config import Settings


SNAPSHOT_SCHEMA_VERSION = 1
_OPERATIONAL_FIELDS = {
    "root",
    "input_recordings_dir",
    "jobs_dir",
    "logs_dir",
    "api_host",
    "api_port",
    "api_parallel_jobs",
    "api_batch_limit",
    "api_allowed_origins",
    "recording_upload_max_bytes",
    "file_stable_seconds",
    "poll_interval_seconds",
}
_SECRET_FIELDS = {
    "google_api_key",
    "openai_api_key",
    "cover_api_key",
    "webhook_url",
}
_PATH_FIELDS = {
    "ffmpeg_path",
    "ffprobe_path",
    "audiowaveform_path",
    "whisper_bin",
    "bgm_path",
    "demucs_path",
    "uvr_path",
}


def snapshot_runtime_settings(settings: Settings) -> dict[str, Any]:
    """Return a deterministic, non-secret configuration snapshot for one run."""
    values: dict[str, Any] = {}
    names = [field.name for field in fields(settings)] if is_dataclass(settings) else list(vars(settings))
    for name in names:
        if name in _OPERATIONAL_FIELDS or name in _SECRET_FIELDS or name.endswith("_api_key"):
            continue
        values[name] = _json_value(getattr(settings, name))
    canonical = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "revision": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "values": values,
    }


def apply_runtime_settings_snapshot(settings: Settings, snapshot: Any) -> Settings:
    """Overlay a persisted run snapshot on current operational settings."""
    if not is_dataclass(settings):
        return settings
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        return settings
    values = snapshot.get("values")
    if not isinstance(values, dict):
        return settings
    known = {field.name for field in fields(settings)}
    updates: dict[str, Any] = {}
    for name, value in values.items():
        if name not in known or name in _OPERATIONAL_FIELDS or name in _SECRET_FIELDS:
            continue
        current = getattr(settings, name)
        updates[name] = _restore_value(name, value, current)
    return replace(settings, **updates)


def _json_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _restore_value(name: str, value: Any, current: Any) -> Any:
    if name in _PATH_FIELDS:
        return Path(value) if value else None
    if name == "subtitle_replacements":
        return tuple(tuple(str(part) for part in pair) for pair in (value or []))
    if isinstance(current, tuple):
        template = current[0] if current else None
        return tuple(_restore_value(name, item, template) for item in (value or []))
    if isinstance(current, dict) and isinstance(value, dict):
        return dict(value)
    if current is None:
        return value
    if isinstance(current, bool):
        return bool(value)
    if isinstance(current, int) and not isinstance(current, bool):
        return int(value)
    if isinstance(current, float):
        return float(value)
    if isinstance(current, Path):
        return Path(value)
    return value
