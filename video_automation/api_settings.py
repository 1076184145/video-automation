from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Protocol

from .credentials import CONFIG_SECRET_REFERENCES, SystemCredentialStore
from .io_utils import write_text_atomic


EDITABLE_ENV_KEYS = {
    "WHISPER_BACKEND",
    "WHISPER_MODEL",
    "WHISPER_MODEL_FALLBACKS",
    "WHISPER_LANGUAGE",
    "WHISPER_INITIAL_PROMPT",
    "FASTER_WHISPER_DEVICE",
    "FASTER_WHISPER_COMPUTE_TYPE",
    "FASTER_WHISPER_BATCH_SIZE",
    "WHISPER_WORD_TIMESTAMPS",
    "WHISPER_VAD_FILTER",
    "TRANSCRIBE_AUDIO_FILTER",
    "SILENCE_THRESHOLD_DB",
    "SILENCE_MIN_LENGTH_SECONDS",
    "SILENCE_MIN_GAP_SECONDS",
    "CUT_MIN_CLIP_SECONDS",
    "CUT_MERGE_GAP_SECONDS",
    "SCENE_THRESHOLD",
    "SOURCE_INTEGRITY_SCAN_ENABLED",
    "ASS_PRESET",
    "ASS_FONT_NAME",
    "ASS_FONT_SIZE",
    "ASS_VERTICAL_FONT_SIZE",
    "ASS_MAX_LINES",
    "ASS_MARGIN_V",
    "ASS_OUTLINE",
    "ASS_SHADOW",
    "SUBTITLE_CENSOR_REPLACEMENT",
    "SUBTITLE_MIN_DURATION_SECONDS",
    "RENDER_VIDEO_ENCODER",
    "RENDER_OUTPUT_FPS",
    "RENDER_X264_PRESET",
    "RENDER_X264_CRF",
    "RENDER_NVENC_PRESET",
    "RENDER_NVENC_CQ",
    "RENDER_NVENC_PREVIEW_PRESET",
    "RENDER_NVENC_PREVIEW_CQ",
    "WEB_PREVIEW_ENABLED",
    "WEB_PREVIEW_MAX_WIDTH",
    "WEB_PREVIEW_MAX_HEIGHT",
    "WEB_PREVIEW_FPS",
    "WEB_PREVIEW_VIDEO_BITRATE",
    "BGM_VOLUME",
    "SOURCE_AUDIO_VOLUME",
    "COVER_PROVIDER",
    "COVER_BASE_URL",
    "COVER_MODEL",
    "COVER_API_KEY",
    "COVER_HTTP_REFERER",
    "COVER_APP_TITLE",
    "COVER_COUNT",
    "COVER_QUALITY",
    "COVER_OUTPUT_FORMAT",
    "COVER_MODALITIES",
    "LLM_PROVIDER",
    "LLM_MODEL",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_BASE_URL",
    "API_BATCH_LIMIT",
    "RECORDING_UPLOAD_MAX_BYTES",
    "NATIVE_WAVEFORM_ENABLED",
    "NATIVE_CUTS_ENABLED",
    "HIGH_QUALITY_AUDIO_ENABLED",
    "AUDIO_SEPARATION_ENGINE",
    "DEMUCS_PATH",
    "DEMUCS_MODEL",
    "DEMUCS_DEVICE",
    "AUDIO_SEPARATION_TIMEOUT_SECONDS",
}

SECRET_ENV_KEYS = frozenset(CONFIG_SECRET_REFERENCES)
_ENV_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


class CredentialBackend(Protocol):
    def get(self, reference: str) -> str | None: ...
    def set(self, reference: str, secret: str) -> None: ...
    def delete(self, reference: str) -> None: ...


class CredentialUpdateError(RuntimeError):
    """Raised when a secret cannot be committed to the OS credential store."""


def normalize_env_updates(raw_updates: dict[str, Any]) -> dict[str, str]:
    updates: dict[str, str] = {}
    for raw_key, raw_value in raw_updates.items():
        key = str(raw_key).strip().upper()
        if key not in EDITABLE_ENV_KEYS:
            raise ValueError(f"setting is not editable: {key}")
        if raw_value is None:
            value = ""
        elif isinstance(raw_value, bool):
            value = "true" if raw_value else "false"
        else:
            value = str(raw_value)
        if "\n" in value or "\r" in value:
            raise ValueError(f"setting cannot contain newlines: {key}")
        if any(ord(char) < 32 and char != "\t" for char in value):
            raise ValueError(f"setting contains invalid control characters: {key}")
        updates[key] = value.strip()
    if not updates:
        raise ValueError("no editable settings provided")
    return updates


def update_env_file(
    root: Path,
    updates: dict[str, str],
    *,
    remove_keys: set[str] | frozenset[str] = frozenset(),
) -> set[str]:
    env_path = root / ".env"
    existing_lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    pending = {str(key).upper(): str(value) for key, value in updates.items()}
    removals = {str(key).upper() for key in remove_keys}
    for key in removals:
        pending.pop(key, None)
    changed: set[str] = set()
    output_lines: list[str] = []
    for line in existing_lines:
        match = _ENV_LINE_RE.match(line)
        if not match:
            output_lines.append(line)
            continue
        key = match.group(1).upper()
        if key in removals:
            changed.add(key)
            continue
        if key not in pending:
            output_lines.append(line)
            continue
        output_lines.append(f"{key}={pending.pop(key)}")
        changed.add(key)
    if pending:
        if output_lines and output_lines[-1].strip():
            output_lines.append("")
        output_lines.append("# Updated from Web Settings")
        for key in sorted(pending):
            output_lines.append(f"{key}={pending[key]}")
            changed.add(key)
    write_text_atomic(env_path, "\n".join(output_lines).rstrip() + "\n")
    return changed


def apply_settings_updates(
    root: Path,
    updates: dict[str, str],
    *,
    credential_store: CredentialBackend | None = None,
) -> set[str]:
    store = credential_store
    regular_updates = {key: value for key, value in updates.items() if key not in SECRET_ENV_KEYS}
    secret_updates = {key: value for key, value in updates.items() if key in SECRET_ENV_KEYS}
    reference_updates: dict[str, str] = {}
    removed_keys: set[str] = set()
    if secret_updates:
        store = store or SystemCredentialStore()
        values = env_file_values(root)
        for key, secret in secret_updates.items():
            reference_key = f"{key}_REF"
            reference = (
                os.environ.get(reference_key)
                or values.get(reference_key)
                or CONFIG_SECRET_REFERENCES[key]
            ).strip()
            try:
                if secret:
                    store.set(reference, secret)
                    reference_updates[reference_key] = reference
                else:
                    store.delete(reference)
                    removed_keys.add(reference_key)
            except Exception as exc:
                raise CredentialUpdateError(f"credential store rejected {key}") from exc
            removed_keys.add(key)
    update_env_file(
        root,
        {**regular_updates, **reference_updates},
        remove_keys=removed_keys,
    )
    return set(updates)


def legacy_secret_keys(root: Path) -> set[str]:
    values = env_file_values(root)
    return {key for key in SECRET_ENV_KEYS if values.get(key, "").strip()}


def migrate_legacy_secrets(
    root: Path,
    *,
    credential_store: CredentialBackend | None = None,
) -> set[str]:
    values = env_file_values(root)
    secrets = {key: values[key] for key in SECRET_ENV_KEYS if values.get(key, "").strip()}
    if not secrets:
        return set()
    store = credential_store or SystemCredentialStore()
    reference_updates: dict[str, str] = {}
    for key, secret in secrets.items():
        reference_key = f"{key}_REF"
        reference = (
            os.environ.get(reference_key)
            or values.get(reference_key)
            or CONFIG_SECRET_REFERENCES[key]
        ).strip()
        try:
            store.set(reference, secret)
        except Exception as exc:
            raise CredentialUpdateError(f"credential store rejected {key}") from exc
        reference_updates[reference_key] = reference
    update_env_file(root, reference_updates, remove_keys=set(secrets))
    return set(secrets)


def env_file_values(root: Path) -> dict[str, str]:
    path = root / ".env"
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().upper()] = value.strip().strip('"').strip("'")
    return values
