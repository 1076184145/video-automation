from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import math
import os
import shutil
import socket
import ssl
import textwrap
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from .config import Settings
from .io_utils import read_json_file, write_json_atomic
from .media import run_command
from .provider_errors import (
    ProviderRequestError,
    provider_configuration_error,
    provider_error_code,
    provider_http_error,
    provider_network_error,
)


ASPECT_SPECS = {
    "9:16": {"slug": "9x16", "selected": "cover_vertical.jpg", "size": "1024x1536", "final": (1080, 1920)},
    "16:9": {"slug": "16x9", "selected": "cover_landscape.jpg", "size": "1536x1024", "final": (1920, 1080)},
}
STYLE_PROMPTS = {
    "short_video": "high-impact creator portrait, strong subject, vivid clean lighting, text-free composition",
    "clean": "minimal editorial scene, clear subject, premium calm lighting, text-free composition",
    "cinematic": "cinematic film-still mood, dramatic lighting, premium text-free composition",
    "gaming": "energetic gaming livestream scene, esports lighting, dynamic text-free composition",
}
SUPPORTED_COVER_PROVIDERS = {"openai", "openai-compatible", "openrouter", "google", "local"}
COVER_SUMMARY_MAX_CHARS = 240
COVER_HIGHLIGHTS_MAX_CHARS = 160
MAX_REMOTE_COVER_IMAGE_BYTES = 20 * 1024 * 1024
MAX_REMOTE_COVER_REDIRECTS = 3
MAX_REMOTE_COVER_TOTAL_SECONDS = 60.0
MAX_COVER_IMAGE_PIXELS = 40_000_000
REMOTE_IMAGE_READ_CHUNK = 64 * 1024


def normalize_cover_options(settings: Settings, payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload or {}
    title = str(payload.get("title") or "").strip()
    style = str(payload.get("style") or "short_video").strip()
    if style not in STYLE_PROMPTS:
        style = "short_video"
    raw_count = payload.get("count", settings.cover_count)
    try:
        count = int(raw_count)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("cover count must be 3 or 5") from exc
    if count not in {3, 5}:
        raise RuntimeError("cover count must be 3 or 5")
    raw_aspects = payload.get("aspects") or list(settings.cover_aspects)
    if isinstance(raw_aspects, str):
        raw_aspects = [part.strip() for part in raw_aspects.split(",")]
    if not isinstance(raw_aspects, (list, tuple)):
        raise RuntimeError("cover aspects must be a list")
    aspects = []
    for aspect in raw_aspects:
        value = str(aspect).strip()
        if value not in ASPECT_SPECS:
            raise RuntimeError(f"unsupported cover aspect: {value}")
        if value not in aspects:
            aspects.append(value)
    if not aspects:
        raise RuntimeError("cover aspects cannot be empty")
    return {"title": title, "style": style, "count": count, "aspects": aspects}


def mark_cover_generation_started(settings: Settings, job_dir: Path, options: dict[str, Any]) -> dict[str, Any]:
    title = _preferred_cover_title(job_dir, str(options.get("title") or ""))
    manifest = _initial_manifest(
        settings,
        job_dir,
        title=title,
        style=str(options.get("style") or "short_video"),
        count=int(options.get("count") or settings.cover_count),
        aspects=list(options.get("aspects") or settings.cover_aspects),
    )
    write_json_atomic(job_dir / "cover_manifest.json", manifest)
    return manifest


def generate_cover_candidates(
    settings: Settings,
    job_dir: Path,
    *,
    title: str = "",
    style: str = "short_video",
    count: int | None = None,
    aspects: list[str] | None = None,
) -> dict[str, Any]:
    try:
        return _generate_provider_cover_candidates(
            settings, job_dir, title=title, style=style, count=count, aspects=aspects
        )
    except Exception as exc:
        if not getattr(settings, "cover_fallback_local", True):
            raise
        return _generate_fallback_covers(
            settings, job_dir, error=exc, title=title, style=style, count=count, aspects=aspects
        )


def _generate_provider_cover_candidates(
    settings: Settings,
    job_dir: Path,
    *,
    title: str = "",
    style: str = "short_video",
    count: int | None = None,
    aspects: list[str] | None = None,
) -> dict[str, Any]:
    provider = settings.cover_provider.strip().lower()
    provider_name = _cover_provider_name(settings)
    if provider not in SUPPORTED_COVER_PROVIDERS:
        raise provider_configuration_error(
            provider_name,
            "image generation",
            "provider_unsupported",
            f"Unsupported COVER_PROVIDER: {settings.cover_provider}",
        )
    if provider != "local" and not settings.cover_api_key_for_provider():
        raise provider_configuration_error(
            provider_name,
            "image generation",
            "credentials_missing",
            "The API key required by the selected cover provider is not configured.",
        )
    if not settings.cover_model.strip():
        raise provider_configuration_error(
            provider_name,
            "image generation",
            "model_missing",
            "COVER_MODEL is not configured.",
        )

    normalized_count = _cover_count(count if count is not None else settings.cover_count)
    normalized_aspects = _cover_aspects(aspects or list(settings.cover_aspects))
    normalized_style = style if style in STYLE_PROMPTS else "short_video"
    manifest_path = job_dir / "cover_manifest.json"
    prompt_title = _preferred_cover_title(job_dir, title)
    reference_path = _prepare_cover_reference(settings, job_dir) if _uses_cover_reference(settings) else None
    context = _cover_context(job_dir, prompt_title)
    manifest = _initial_manifest(
        settings,
        job_dir,
        title=prompt_title,
        style=normalized_style,
        count=normalized_count,
        aspects=normalized_aspects,
    )
    if reference_path is not None:
        manifest["reference_image"] = reference_path.name
    write_json_atomic(manifest_path, manifest)

    try:
        for aspect in normalized_aspects:
            spec = ASPECT_SPECS[aspect]
            prompt = _build_prompt(context, aspect, normalized_style)
            payload = _generate_images(
                settings,
                prompt,
                normalized_count,
                spec["size"],
                reference_path=reference_path,
            )
            candidates = []
            for index, item in enumerate(payload.get("data") or [], start=1):
                raw = item.get("b64_json")
                if not raw:
                    continue
                filename = f"cover_{spec['slug']}_{index:02}.jpg"
                output_path = job_dir / filename
                _postprocess_cover(
                    _decode_image_data(raw),
                    output_path,
                    size=spec["final"],
                    title=prompt_title,
                    font_name=settings.cover_title_font,
                    output_format=settings.cover_output_format,
                )
                candidates.append({
                    "file": filename,
                    "aspect": aspect,
                    "width": spec["final"][0],
                    "height": spec["final"][1],
                    "revised_prompt": item.get("revised_prompt", ""),
                })
            manifest["candidates"][aspect] = candidates
        manifest["status"] = "ready"
        manifest["updated_at"] = _now()
        manifest["error_code"] = ""
        manifest["error"] = ""
        write_json_atomic(manifest_path, manifest)
        return manifest
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["updated_at"] = _now()
        manifest["error_code"] = provider_error_code(exc)
        manifest["error"] = str(exc)
        write_json_atomic(manifest_path, manifest)
        raise
    finally:
        if provider == "local":
            from .local_ai import release_local_ai

            release_local_ai()


def _generate_fallback_covers(
    settings: Settings,
    job_dir: Path,
    *,
    error: Exception,
    title: str = "",
    style: str = "short_video",
    count: int | None = None,
    aspects: list[str] | None = None,
) -> dict[str, Any]:
    """Zero-provider covers: composite the best video frame with the title panel.

    Used when the configured image-generation provider is unavailable so the
    job still ships reviewable covers; the manifest records that these are
    fallback candidates.
    """
    normalized_count = _cover_count(count if count is not None else settings.cover_count)
    normalized_aspects = _cover_aspects(aspects or list(settings.cover_aspects))
    normalized_style = style if style in STYLE_PROMPTS else "short_video"
    prompt_title = _preferred_cover_title(job_dir, title)
    manifest = _initial_manifest(
        settings,
        job_dir,
        title=prompt_title,
        style=normalized_style,
        count=normalized_count,
        aspects=normalized_aspects,
    )
    manifest["generator"] = "fallback_frame_composite"
    manifest["fallback_reason"] = provider_error_code(error)
    manifest["fallback_error"] = str(error)[:800]
    manifest_path = job_dir / "cover_manifest.json"

    reference = _prepare_cover_reference(settings, job_dir)
    if reference is None or not Path(reference).is_file():
        manifest["status"] = "failed"
        manifest["updated_at"] = _now()
        manifest["error_code"] = provider_error_code(error)
        manifest["error"] = str(error)
        manifest["fallback_error"] = (
            str(manifest.get("fallback_error") or "")
            + " | No usable video frame is available for a fallback cover."
        )
        write_json_atomic(manifest_path, manifest)
        raise error

    raw = Path(reference).read_bytes()
    variants: list[tuple[str, float]] = [("frame", 0.0), ("frame_dark", 0.45)][: max(1, min(normalized_count, 2))]
    try:
        for aspect in normalized_aspects:
            spec = ASPECT_SPECS[aspect]
            candidates = []
            for index, (variant, darkening) in enumerate(variants, start=1):
                filename = f"cover_{spec['slug']}_{index:02}.jpg"
                output_path = job_dir / filename
                _postprocess_cover(
                    _darken_cover_frame(raw, darkening) if darkening else raw,
                    output_path,
                    size=spec["final"],
                    title=prompt_title,
                    font_name=settings.cover_title_font,
                    output_format=settings.cover_output_format,
                )
                candidates.append({
                    "file": filename,
                    "aspect": aspect,
                    "width": spec["final"][0],
                    "height": spec["final"][1],
                    "revised_prompt": "",
                    "fallback_variant": variant,
                })
            manifest["candidates"][aspect] = candidates
        manifest["status"] = "ready"
        manifest["updated_at"] = _now()
        manifest["error_code"] = ""
        manifest["error"] = ""
        write_json_atomic(manifest_path, manifest)
        return manifest
    except Exception:
        manifest["status"] = "failed"
        manifest["updated_at"] = _now()
        manifest["error_code"] = provider_error_code(error)
        manifest["error"] = str(error)
        write_json_atomic(manifest_path, manifest)
        raise error


def _darken_cover_frame(raw: bytes, factor: float) -> bytes:
    try:
        from PIL import Image, ImageEnhance
    except ImportError:
        return raw
    with Image.open(BytesIO(raw)) as image:
        darkened = ImageEnhance.Brightness(image.convert("RGB")).enhance(max(0.0, 1.0 - factor))
        buffer = BytesIO()
        darkened.save(buffer, format="JPEG", quality=92)
        return buffer.getvalue()


def select_cover(job_dir: Path, *, aspect: str, candidate: str) -> dict[str, Any]:
    if aspect not in ASPECT_SPECS:
        raise RuntimeError(f"unsupported cover aspect: {aspect}")
    source = (job_dir / Path(candidate).name).resolve()
    try:
        source.relative_to(job_dir.resolve())
    except ValueError as exc:
        raise RuntimeError("invalid cover candidate") from exc
    if not source.is_file():
        raise RuntimeError("cover candidate not found")
    spec = ASPECT_SPECS[aspect]
    if not source.name.lower().startswith(f"cover_{spec['slug']}_"):
        raise RuntimeError("cover candidate does not match requested aspect")
    target = job_dir / str(spec["selected"])
    _copy_file_atomic(source, target)
    manifest = read_json_file(job_dir / "cover_manifest.json") or {}
    selected = manifest.get("selected") if isinstance(manifest.get("selected"), dict) else {}
    selected[aspect] = target.name
    manifest["selected"] = selected
    manifest["updated_at"] = _now()
    if "status" not in manifest:
        manifest["status"] = "ready"
    write_json_atomic(job_dir / "cover_manifest.json", manifest)
    return manifest


def cover_manifest(job_dir: Path) -> dict[str, Any]:
    return read_json_file(job_dir / "cover_manifest.json") or {
        "status": "idle",
        "candidates": {},
        "selected": _selected_from_existing(job_dir),
        "error_code": "",
        "error": "",
    }


def _initial_manifest(
    settings: Settings,
    job_dir: Path,
    *,
    title: str,
    style: str,
    count: int,
    aspects: list[str],
) -> dict[str, Any]:
    now = _now()
    return {
        "status": "generating",
        "provider": settings.cover_provider,
        "model": settings.cover_model,
        "title": title,
        "style": style,
        "count": count,
        "aspects": aspects,
        "quality": settings.cover_quality,
        "output_format": settings.cover_output_format,
        "started_at": now,
        "updated_at": now,
        "candidates": {},
        "selected": _selected_from_existing(job_dir),
    }


def _generate_images(
    settings: Settings,
    prompt: str,
    count: int,
    size: str,
    *,
    reference_path: Path | None = None,
) -> dict[str, Any]:
    if settings.cover_provider.strip().lower() == "local":
        from .local_ai import generate_local_cover_images

        return generate_local_cover_images(
            settings,
            prompt=prompt,
            count=count,
            aspect=_aspect_from_size(size),
            reference_path=reference_path,
        )
    if settings.cover_provider.strip().lower() == "google":
        return _google_generate_images(settings, prompt, count, _aspect_from_size(size))
    return _openai_generate_images(
        settings,
        prompt,
        count,
        size,
        reference_path=reference_path,
    )


def _openai_generate_images(
    settings: Settings,
    prompt: str,
    count: int,
    size: str,
    *,
    reference_path: Path | None = None,
) -> dict[str, Any]:
    if _uses_openrouter_images(settings):
        return _openrouter_generate_images(
            settings,
            prompt,
            count,
            _aspect_from_size(size),
            reference_path=reference_path,
        )
    output_format = settings.cover_output_format if settings.cover_output_format in {"jpeg", "png", "webp"} else "jpeg"
    body = {
        "model": settings.cover_model,
        "prompt": prompt,
        "n": count,
        "size": size,
        "quality": settings.cover_quality,
        "output_format": output_format,
        "background": "opaque",
    }
    request = urllib.request.Request(
        _join_url(settings.cover_base_url, "images/generations"),
        data=json.dumps(body).encode("utf-8"),
        headers=_cover_headers(settings),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise provider_http_error(
            _cover_provider_name(settings),
            "image generation",
            exc.code,
            detail,
        ) from exc
    except OSError as exc:
        raise provider_network_error(
            _cover_provider_name(settings),
            "image generation",
            exc,
        ) from exc


def _openrouter_generate_images(
    settings: Settings,
    prompt: str,
    count: int,
    aspect: str,
    *,
    reference_path: Path | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": settings.cover_model,
        "prompt": prompt,
        "n": count,
        "aspect_ratio": aspect,
        "quality": settings.cover_quality,
        "output_format": settings.cover_output_format,
        "background": "opaque",
    }
    if reference_path is not None and reference_path.is_file():
        body["input_references"] = [{
            "type": "image_url",
            "image_url": {"url": _image_data_url(reference_path)},
        }]
    request = urllib.request.Request(
        _join_url(settings.cover_base_url, "images"),
        data=json.dumps(body).encode("utf-8"),
        headers=_cover_headers(settings),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise provider_http_error(
            "OpenRouter",
            "image generation",
            exc.code,
            detail,
        ) from exc
    except OSError as exc:
        raise provider_network_error("OpenRouter", "image generation", exc) from exc
    raw_data = payload.get("data") if isinstance(payload.get("data"), list) else []
    data = []
    for item in raw_data:
        if not isinstance(item, dict):
            continue
        raw = item.get("b64_json") or item.get("url")
        if isinstance(raw, str) and raw.strip():
            data.append({
                "b64_json": raw,
                "revised_prompt": str(item.get("revised_prompt") or ""),
            })
    if not data:
        raise ProviderRequestError(
            "OpenRouter",
            "image generation",
            "response_invalid",
            "The response did not include generated images. "
            "Check that COVER_MODEL supports the dedicated Image API.",
        )
    return {"data": data}


def _google_generate_images(settings: Settings, prompt: str, count: int, aspect: str) -> dict[str, Any]:
    api_key = settings.cover_api_key_for_provider()
    if not api_key:
        raise provider_configuration_error(
            "Google Gemini",
            "image generation",
            "credentials_missing",
            "COVER_API_KEY or GOOGLE_API_KEY is not configured.",
        )
    data: list[dict[str, Any]] = []
    for _ in range(count):
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": aspect},
            },
        }
        request = urllib.request.Request(
            _google_model_url(settings.google_base_url, settings.cover_model),
            data=json.dumps(body).encode("utf-8"),
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise provider_http_error(
                "Google Gemini",
                "image generation",
                exc.code,
                detail,
            ) from exc
        except OSError as exc:
            raise provider_network_error("Google Gemini", "image generation", exc) from exc
        images, content = _google_images(payload)
        data.extend({"b64_json": raw, "revised_prompt": content} for raw in images)
    return {"data": data}


def _google_images(payload: dict[str, Any]) -> tuple[list[str], str]:
    images: list[str] = []
    text_parts: list[str] = []
    for candidate in payload.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        if not isinstance(content, dict):
            continue
        for part in content.get("parts", []):
            if not isinstance(part, dict):
                continue
            if isinstance(part.get("text"), str):
                text_parts.append(part["text"])
            inline = part.get("inlineData") or part.get("inline_data")
            if isinstance(inline, dict) and isinstance(inline.get("data"), str):
                images.append(inline["data"])
    if not images:
        raise ProviderRequestError(
            "Google Gemini",
            "image generation",
            "response_invalid",
            "The response did not include generated image data. "
            "Check that COVER_MODEL supports image output.",
        )
    return images, "\n".join(text_parts).strip()


def _google_model_url(base_url: str, model: str) -> str:
    base = (base_url or "https://generativelanguage.googleapis.com/v1beta").strip().rstrip("/")
    model_name = model.strip()
    if model_name.startswith("models/"):
        model_name = model_name.removeprefix("models/")
    return f"{base}/models/{model_name}:generateContent"


def _cover_headers(settings: Settings) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {settings.cover_api_key_for_provider()}",
        "Content-Type": "application/json",
    }
    if settings.cover_http_referer.strip():
        headers["HTTP-Referer"] = settings.cover_http_referer.strip()
    if settings.cover_app_title.strip():
        headers["X-Title"] = settings.cover_app_title.strip()
    return headers


def _openrouter_images(payload: dict[str, Any]) -> tuple[list[str], str]:
    choices = payload.get("choices") if isinstance(payload.get("choices"), list) else []
    images: list[str] = []
    content = ""
    for choice in choices:
        message = choice.get("message") if isinstance(choice, dict) else None
        if not isinstance(message, dict):
            continue
        if isinstance(message.get("content"), str):
            content = message["content"]
        raw_images = message.get("images") if isinstance(message.get("images"), list) else []
        for item in raw_images:
            if not isinstance(item, dict):
                continue
            image_url = item.get("image_url") or item.get("imageUrl")
            if isinstance(image_url, dict) and isinstance(image_url.get("url"), str):
                images.append(image_url["url"])
    if not images:
        raise RuntimeError("OpenRouter response did not include generated images. Check that COVER_MODEL supports image output and COVER_MODALITIES matches the model.")
    return images, content


def _uses_openrouter_images(settings: Settings) -> bool:
    provider = settings.cover_provider.strip().lower()
    return provider == "openrouter" or "openrouter.ai" in settings.cover_base_url.strip().lower()


def _uses_cover_reference(settings: Settings) -> bool:
    return settings.cover_provider.strip().lower() == "local" or _uses_openrouter_images(settings)


def _cover_provider_name(settings: Settings) -> str:
    provider = settings.cover_provider.strip().lower()
    if provider == "local":
        return "Local Hugging Face"
    if provider == "openrouter" or "openrouter.ai" in settings.cover_base_url.strip().lower():
        return "OpenRouter"
    if provider == "google":
        return "Google Gemini"
    if provider in {"openai", "openai-compatible"}:
        return "OpenAI" if provider == "openai" else "OpenAI-compatible provider"
    return settings.cover_provider.strip() or "Cover provider"


def _aspect_from_size(size: str) -> str:
    if size.startswith("1024x1536"):
        return "9:16"
    if size.startswith("1536x1024"):
        return "16:9"
    return "1:1"


def _decode_image_data(raw: str) -> bytes:
    value = raw.strip()
    if value.startswith("data:") and "," in value:
        value = value.split(",", 1)[1]
        return base64.b64decode(value)
    if value.startswith(("http://", "https://")):
        return _fetch_remote_image(value)
    return base64.b64decode(value)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: str, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self._validated_address = address

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._validated_address, self.port),
            self.timeout,
            self.source_address,
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: str, timeout: float):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._validated_address = address

    def connect(self) -> None:
        sock = socket.create_connection(
            (self._validated_address, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _remote_image_connection(parsed, address: str, timeout: float):  # type: ignore[no-untyped-def]
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    connection_type = _PinnedHTTPSConnection if parsed.scheme == "https" else _PinnedHTTPConnection
    return connection_type(parsed.hostname, port, address, timeout)


def _fetch_remote_image(
    url: str,
    *,
    resolve=None,
    connection_factory=None,
    clock=None,
) -> bytes:
    current = url
    resolver = resolve or socket.getaddrinfo
    make_connection = connection_factory or _remote_image_connection
    monotonic = clock or time.monotonic
    deadline = monotonic() + MAX_REMOTE_COVER_TOTAL_SECONDS
    for _ in range(MAX_REMOTE_COVER_REDIRECTS + 1):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise RuntimeError("remote cover image exceeded the total deadline")
        parsed, addresses = _resolve_remote_image_url(current, resolver)
        connection = make_connection(parsed, addresses[0], remaining)
        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        try:
            connection.request("GET", target, headers={"User-Agent": "VideoAutomation/1.0"})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                if not location:
                    raise RuntimeError("remote cover image redirect had no Location")
                current = urljoin(current, location)
                continue
            if not 200 <= response.status < 300:
                raise RuntimeError(f"remote cover image returned HTTP {response.status}")
            length = response.headers.get("Content-Length")
            try:
                declared_length = int(length) if length else None
            except ValueError as exc:
                raise RuntimeError("remote cover image returned an invalid Content-Length") from exc
            if declared_length is not None and declared_length > MAX_REMOTE_COVER_IMAGE_BYTES:
                raise RuntimeError("remote cover image is too large")
            data = bytearray()
            while len(data) <= MAX_REMOTE_COVER_IMAGE_BYTES:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise RuntimeError("remote cover image exceeded the total deadline")
                sock = getattr(connection, "sock", None)
                if sock is not None:
                    sock.settimeout(max(0.1, min(5.0, remaining)))
                chunk = response.read(
                    min(REMOTE_IMAGE_READ_CHUNK, MAX_REMOTE_COVER_IMAGE_BYTES + 1 - len(data))
                )
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) > MAX_REMOTE_COVER_IMAGE_BYTES:
                raise RuntimeError("remote cover image is too large")
            return bytes(data)
        except (OSError, http.client.HTTPException) as exc:
            raise RuntimeError("remote cover image request failed") from exc
        finally:
            connection.close()
    raise RuntimeError("remote cover image had too many redirects")


def _resolve_remote_image_url(url: str, resolve):  # type: ignore[no-untyped-def]
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise RuntimeError("remote cover image URL must be http or https")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        resolved = resolve(parsed.hostname, port, type=socket.SOCK_STREAM)
    except (OSError, ValueError) as exc:
        raise RuntimeError("remote cover image host could not be resolved") from exc
    addresses = []
    for *_, sockaddr in resolved:
        address = str(sockaddr[0])
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise RuntimeError("remote cover image URL resolves to a private or local address")
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise RuntimeError("remote cover image host could not be resolved")
    return parsed, addresses


def _validate_remote_image_url(url: str) -> None:
    _resolve_remote_image_url(url, socket.getaddrinfo)


def _validate_cover_image_dimensions(width: int, height: int) -> None:
    if width <= 0 or height <= 0 or width * height > MAX_COVER_IMAGE_PIXELS:
        raise RuntimeError("cover image exceeds the decoded pixel limit")


def _join_url(base_url: str, path: str) -> str:
    base = (base_url or "https://api.openai.com/v1").strip().rstrip("/")
    suffix = path.strip("/")
    return f"{base}/{suffix}"


def _postprocess_cover(raw: bytes, output_path: Path, *, size: tuple[int, int], title: str, font_name: str, output_format: str) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("cover generation requires Pillow; install requirements-optional.txt") from exc

    with Image.open(BytesIO(raw)) as source:
        _validate_cover_image_dimensions(source.width, source.height)
        image = _cover_resize(source.convert("RGB"), size)
    if title:
        draw = ImageDraw.Draw(image, "RGBA")
        font = _cover_font(ImageFont, font_name, max(44, int(size[0] * 0.06)))
        lines = _wrap_title(draw, title, font, max_width=int(size[0] * 0.84))
        line_height = _text_height(draw, "测", font) + int(size[1] * 0.014)
        panel_top = int(size[1] * 0.68)
        draw.rectangle([0, panel_top, size[0], size[1]], fill=(8, 10, 14, 255))
        text_height = line_height * len(lines)
        y = panel_top + max(0, (size[1] - panel_top - text_height) // 2)
        for line in lines:
            width = _text_width(draw, line, font)
            x = (size[0] - width) / 2
            for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
                draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 210))
            draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
            y += line_height
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp")
    normalized_format = output_format.strip().lower()
    if normalized_format not in {"jpeg", "png", "webp"}:
        normalized_format = "jpeg"
    save_format = "JPEG" if normalized_format == "jpeg" else normalized_format.upper()
    save_kwargs: dict[str, Any] = {}
    if normalized_format in {"jpeg", "webp"}:
        save_kwargs["quality"] = 92
    image.save(tmp_path, format=save_format, **save_kwargs)
    os.replace(tmp_path, output_path)


def _cover_resize(image: Any, size: tuple[int, int]) -> Any:
    width, height = image.size
    target_width, target_height = size
    scale = max(target_width / width, target_height / height)
    next_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    resized = image.resize(next_size)
    left = max(0, (next_size[0] - target_width) // 2)
    top = max(0, (next_size[1] - target_height) // 2)
    return resized.crop((left, top, left + target_width, top + target_height))


def _cover_font(image_font: Any, font_name: str, size: int) -> Any:
    candidates = []
    path = Path(font_name)
    if path.exists():
        candidates.append(path)
    windows_fonts = Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts"
    candidates.extend([
        windows_fonts / "msyh.ttc",
        windows_fonts / "msyhbd.ttc",
        windows_fonts / "simhei.ttf",
        windows_fonts / "arial.ttf",
    ])
    for candidate in candidates:
        try:
            return image_font.truetype(str(candidate), size=size)
        except Exception:
            continue
    return image_font.load_default()


def _wrap_title(draw: Any, title: str, font: Any, *, max_width: int) -> list[str]:
    text = " ".join(title.strip().split())
    if not text:
        return []
    if all(ord(char) < 128 for char in text):
        chunks = textwrap.wrap(text, width=22) or [text]
    else:
        chunks = _wrap_cjk(draw, text, font, max_width)
    lines = []
    for chunk in chunks:
        if _text_width(draw, chunk, font) <= max_width:
            lines.append(chunk)
        else:
            lines.extend(_wrap_cjk(draw, chunk, font, max_width))
    return lines[:3]


def _wrap_cjk(draw: Any, text: str, font: Any, max_width: int) -> list[str]:
    lines = []
    current = ""
    for char in text:
        candidate = f"{current}{char}"
        if current and _text_width(draw, candidate, font) > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _text_width(draw: Any, text: str, font: Any) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return int(box[2] - box[0])


def _text_height(draw: Any, text: str, font: Any) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return int(box[3] - box[1])


def _build_prompt(context: dict[str, Any], aspect: str, style: str) -> str:
    style_prompt = STYLE_PROMPTS.get(style, STYLE_PROMPTS["short_video"])
    return (
        f"Create one polished, text-free editorial portrait or scene. Aspect ratio: {aspect}. "
        f"Style: {style_prompt}. "
        "Generate natural background art, not a poster, advertisement, screenshot, graphic design, or cover layout. "
        "Show only the visual scene; the application adds the title separately after generation. "
        "Use the notes below only as private art direction and never render or copy their wording. "
        "Do not draw text, letters, numbers, captions, logos, watermarks, UI, badges, screenshots, or text-like marks. "
        "Base the scene on the supplied content; do not invent public figures, brands, or unrelated events. "
        "If a reference frame is supplied, preserve the creator's recognizable identity, clothing, and room context. "
        "Keep the lower third visually simple for a later title overlay. "
        f"Concept note: {context['title']}. "
        f"Context note: {context['summary']}. "
        f"Moment notes: {context['highlights']}. "
        f"Reference cue: {context['thumbnail']}."
    )


def _cover_context(job_dir: Path, title: str) -> dict[str, str]:
    manifest = read_json_file(job_dir / "manifest.json") or {}
    cuts = read_json_file(job_dir / "cuts.json") or {}
    transcript = read_json_file(job_dir / "transcript.json") or {}
    semantic_payload = read_json_file(job_dir / "highlights.json") or {}
    metadata = read_json_file(job_dir / "metadata.json") or {}
    source_name = manifest.get("source_name") or job_dir.name
    segments = transcript.get("segments") if isinstance(transcript.get("segments"), list) else []
    transcript_text = " ".join(
        str(segment.get("text", "")).strip()
        for segment in _sample_evenly_for_cover(
            [item for item in segments if isinstance(item, dict)],
            24,
        )
        if str(segment.get("text", "")).strip()
    )
    semantic = semantic_payload.get("highlights")
    if not isinstance(semantic, list) or not semantic:
        semantic = cuts.get("semantic_highlights")
    if not isinstance(semantic, list):
        semantic = []
    ranked_semantic = sorted(
        [item for item in semantic if isinstance(item, dict)],
        key=lambda item: _cover_score(item.get("score")),
        reverse=True,
    )[:5]
    semantic_moments = []
    for item in ranked_semantic:
        start = _cover_score(item.get("start"))
        end = _cover_score(item.get("end"))
        evidence = _cover_transcript_text(segments, start, end, max_chars=220)
        reason = str(item.get("reason") or "").strip()
        recommended_use = str(item.get("recommended_use") or "").strip()
        parts = [
            f"{start:.1f}-{end:.1f}s",
            evidence,
            f"亮点：{reason}" if reason else "",
            f"用途：{recommended_use}" if recommended_use else "",
        ]
        semantic_moments.append("；".join(part for part in parts if part))

    clips = cuts.get("clips") if isinstance(cuts.get("clips"), list) else []
    best = sorted(
        [clip for clip in clips if isinstance(clip, dict)],
        key=lambda clip: _cover_score(
            clip.get("final_score")
            if clip.get("final_score") is not None
            else clip.get("content_score")
        ),
        reverse=True,
    )[:5]
    structural_moments = [
        str(
            clip.get("subtitle_text")
            or clip.get("transcript_text")
            or clip.get("reason")
            or ""
        ).strip()[:140]
        for clip in best
    ]
    semantic_summary = str(semantic_payload.get("summary") or "").strip()
    metadata_descriptions = metadata.get("descriptions") if isinstance(metadata.get("descriptions"), list) else []
    metadata_summary = next(
        (str(item).strip() for item in metadata_descriptions if str(item).strip()),
        "",
    )
    highlights = " / ".join(semantic_moments or [item for item in structural_moments if item])
    summary = semantic_summary or metadata_summary or transcript_text or str(source_name)
    return {
        "title": title or _preferred_cover_title(job_dir, ""),
        "summary": summary[:COVER_SUMMARY_MAX_CHARS],
        "highlights": (
            highlights or "important commentary moments from the video"
        )[:COVER_HIGHLIGHTS_MAX_CHARS],
        "thumbnail": _thumbnail_summary(job_dir),
    }


def _preferred_cover_title(job_dir: Path, explicit_title: str) -> str:
    title = " ".join(str(explicit_title or "").split())
    if title:
        return title
    metadata = read_json_file(job_dir / "metadata.json") or {}
    for key in ("cover_titles", "titles"):
        values = metadata.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            candidate = " ".join(str(value or "").split())
            if candidate:
                return candidate[:120]
    semantic_payload = read_json_file(job_dir / "highlights.json") or {}
    semantic_summary = " ".join(str(semantic_payload.get("summary") or "").split())
    if semantic_summary:
        concise_title = _summary_cover_title(semantic_summary)
        if concise_title:
            return concise_title
    return _default_title(job_dir)


def _summary_cover_title(summary: str, max_chars: int = 24) -> str:
    text = " ".join(str(summary or "").split())
    separators = ("，", ",", "。", "！", "？", "；", ".", "!", "?", ";")
    positions = [text.find(separator) for separator in separators if separator in text]
    if positions:
        text = text[: min(position for position in positions if position >= 0)]
    return text[:max_chars].rstrip("，,、：:；; ")


def _sample_evenly_for_cover(items: list[Any], limit: int) -> list[Any]:
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


def _cover_transcript_text(
    segments: list[Any],
    start: float,
    end: float,
    *,
    max_chars: int,
) -> str:
    texts = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        segment_start = _cover_score(segment.get("start"))
        segment_end = _cover_score(segment.get("end"))
        if segment_start < end and segment_end > start:
            text = str(segment.get("text") or "").strip()
            if text:
                texts.append(text)
    return " ".join(texts)[:max_chars]


def _cover_score(value: Any) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _prepare_cover_reference(settings: Settings, job_dir: Path) -> Path | None:
    fallback = job_dir / "thumbnail.jpg"
    manifest = read_json_file(job_dir / "manifest.json") or {}
    source_value = str(manifest.get("source_path") or "").strip()
    source_path = Path(source_value) if source_value else None
    intervals = _cover_reference_intervals(job_dir)
    if source_path is None or not source_path.is_file() or not intervals:
        return fallback if fallback.is_file() else None
    interval_signature = [
        [round(start, 3), round(end, 3), round(semantic_score, 3)]
        for start, end, semantic_score in intervals
    ]

    output_path = job_dir / "highlight_thumbnail.jpg"
    metadata_path = job_dir / "highlight_thumbnail.json"
    cached = read_json_file(metadata_path) or {}
    if (
        output_path.is_file()
        and output_path.stat().st_size > 0
        and cached.get("selection_algorithm") == "semantic_visual_v3"
        and cached.get("candidate_intervals") == interval_signature
    ):
        return output_path

    candidates = []
    max_semantic_score = max(item[2] for item in intervals)
    candidate_index = 0
    for interval_start, interval_end, semantic_score in intervals:
        timestamps = _cover_reference_sample_timestamps(interval_start, interval_end)
        midpoint = (interval_start + interval_end) / 2
        for timestamp in timestamps:
            candidate_index += 1
            temp_path = job_dir / f".highlight_thumbnail.candidate-{candidate_index:02}.jpg"
            temp_path.unlink(missing_ok=True)
            if not _extract_cover_reference_frame(settings, source_path, temp_path, timestamp):
                temp_path.unlink(missing_ok=True)
                continue
            metrics = _cover_reference_frame_score(temp_path)
            semantic_adjustment = (semantic_score - max_semantic_score) * 1.25
            candidates.append({
                "path": temp_path,
                "timestamp": timestamp,
                "interval_start": interval_start,
                "interval_end": interval_end,
                "interval_midpoint": midpoint,
                "semantic_score": semantic_score,
                "semantic_adjustment": semantic_adjustment,
                "selection_score": float(metrics.get("score") or 0.0) + semantic_adjustment,
                **metrics,
            })
    if not candidates:
        return fallback if fallback.is_file() else None
    selected = max(
        candidates,
        key=lambda item: (
            float(item.get("selection_score") or 0.0),
            float(item.get("semantic_score") or 0.0),
            -abs(float(item["timestamp"]) - float(item["interval_midpoint"])),
        ),
    )
    os.replace(Path(selected["path"]), output_path)
    for candidate in candidates:
        path = Path(candidate["path"])
        if path != Path(selected["path"]):
            path.unlink(missing_ok=True)
    write_json_atomic(metadata_path, {
        "status": "ready",
        "path": output_path.name,
        "selection_algorithm": "semantic_visual_v3",
        "timestamp": round(float(selected["timestamp"]), 3),
        "interval_start": round(float(selected["interval_start"]), 3),
        "interval_end": round(float(selected["interval_end"]), 3),
        "semantic_score": round(float(selected["semantic_score"]), 3),
        "candidate_intervals": interval_signature,
        "sampled_timestamps": [
            round(float(candidate["timestamp"]), 3)
            for candidate in candidates
        ],
        "selection_score": round(float(selected.get("selection_score") or 0.0), 3),
        "visual_score": round(float(selected.get("score") or 0.0), 3),
        "face_count": selected.get("face_count"),
        "face_ratio": selected.get("face_ratio"),
        "face_center_distance": selected.get("face_center_distance"),
        "sharpness": round(float(selected.get("sharpness") or 0.0), 3),
        "brightness": round(float(selected.get("brightness") or 0.0), 3),
        "repeated_thirds": round(float(selected.get("repeated_thirds") or 0.0), 4),
        "source_name": manifest.get("source_name") or source_path.name,
        "generated_at": _now(),
    })
    return output_path


def _extract_cover_reference_frame(
    settings: Settings,
    source_path: Path,
    output_path: Path,
    timestamp: float,
) -> bool:
    result = run_command([
        str(settings.ffmpeg_path),
        "-hide_banner",
        "-y",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        str(source_path),
        "-frames:v",
        "1",
        "-vf",
        "scale=1280:-2",
        "-q:v",
        "2",
        str(output_path),
    ], timeout=120)
    return result.returncode == 0 and output_path.is_file() and output_path.stat().st_size > 0


def _cover_reference_frame_score(path: Path) -> dict[str, Any]:
    try:
        import cv2

        image = cv2.imread(str(path))
        if image is None:
            raise RuntimeError("OpenCV could not decode the frame")
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        cascade_path = str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
        detector = cv2.CascadeClassifier(cascade_path)
        faces = detector.detectMultiScale(
            gray,
            scaleFactor=1.08,
            minNeighbors=5,
            minSize=(48, 48),
        )
        height, width = gray.shape[:2]
        face_count = len(faces)
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        brightness = float(gray.mean())
        repeated_thirds = _repeated_vertical_thirds_score(gray)
        face_ratio = 0.0
        face_center_distance = None
        score = (
            min(30.0, sharpness / 18.0)
            - abs(brightness - 135.0) / 8.0
            - repeated_thirds * 140.0
        )
        if face_count == 1:
            x, y, face_width, face_height = faces[0]
            face_ratio = float(face_width * face_height) / max(1.0, float(width * height))
            center_x = x + face_width / 2
            center_y = y + face_height / 2
            horizontal_distance = abs(center_x / max(1.0, width) - 0.5)
            vertical_distance = abs(center_y / max(1.0, height) - 0.42)
            face_center_distance = horizontal_distance + vertical_distance
            score += (
                120.0
                + min(35.0, face_ratio * 260.0)
                - horizontal_distance * 110.0
                - vertical_distance * 40.0
            )
        elif face_count > 1:
            score += max(-40.0, 35.0 - (face_count - 1) * 35.0)
        else:
            score -= 25.0
        return {
            "score": round(score, 3),
            "face_count": int(face_count),
            "sharpness": round(sharpness, 3),
            "brightness": round(brightness, 3),
            "repeated_thirds": round(repeated_thirds, 4),
            "face_ratio": round(face_ratio, 5),
            "face_center_distance": (
                round(face_center_distance, 5)
                if face_center_distance is not None
                else None
            ),
        }
    except Exception:
        try:
            from PIL import Image, ImageFilter, ImageStat

            with Image.open(path) as image:
                gray = image.convert("L")
                brightness = float(ImageStat.Stat(gray).mean[0])
                edges = gray.filter(ImageFilter.FIND_EDGES)
                sharpness = float(ImageStat.Stat(edges).var[0])
                repeated_thirds = _repeated_vertical_thirds_pillow_score(gray)
            return {
                "score": round(
                    min(30.0, sharpness / 8.0)
                    - abs(brightness - 135.0) / 8.0
                    - repeated_thirds * 140.0,
                    3,
                ),
                "face_count": None,
                "sharpness": round(sharpness, 3),
                "brightness": round(brightness, 3),
                "repeated_thirds": round(repeated_thirds, 4),
                "face_ratio": None,
                "face_center_distance": None,
            }
        except Exception:
            return {
                "score": 0.0,
                "face_count": None,
                "sharpness": 0.0,
                "brightness": 0.0,
                "repeated_thirds": 0.0,
                "face_ratio": None,
                "face_center_distance": None,
            }


def _repeated_vertical_thirds_score(gray: Any) -> float:
    try:
        import cv2

        height, width = gray.shape[:2]
        third = width // 3
        if height < 32 or third < 32:
            return 0.0
        regions = [
            gray[:, index * third:(index + 1) * third]
            for index in range(3)
        ]
        normalized = [cv2.resize(region, (160, 90)) for region in regions]
        differences = []
        for left, right in ((normalized[0], normalized[1]), (normalized[1], normalized[2])):
            difference = float(cv2.absdiff(left, right).mean())
            differences.append(difference)
        mean_difference = sum(differences) / len(differences)
        return max(0.0, min(1.0, (28.0 - mean_difference) / 20.0))
    except Exception:
        return 0.0


def _repeated_vertical_thirds_pillow_score(gray: Any) -> float:
    try:
        from PIL import ImageChops, ImageStat

        width, height = gray.size
        third = width // 3
        if height < 32 or third < 32:
            return 0.0
        regions = [
            gray.crop((index * third, 0, (index + 1) * third, height)).resize((160, 90))
            for index in range(3)
        ]
        differences = [
            float(ImageStat.Stat(ImageChops.difference(left, right)).mean[0])
            for left, right in ((regions[0], regions[1]), (regions[1], regions[2]))
        ]
        mean_difference = sum(differences) / len(differences)
        return max(0.0, min(1.0, (28.0 - mean_difference) / 20.0))
    except Exception:
        return 0.0


def _cover_reference_sample_timestamps(start: float, end: float) -> list[float]:
    duration = max(0.0, end - start)
    fractions = (0.5,) if duration <= 4.0 else (0.15, 0.35, 0.5, 0.65, 0.85)
    return list(dict.fromkeys(round(start + duration * fraction, 3) for fraction in fractions))


def _cover_reference_timestamp(job_dir: Path) -> float | None:
    interval = _cover_reference_interval(job_dir)
    return round((interval[0] + interval[1]) / 2, 3) if interval is not None else None


def _cover_reference_interval(job_dir: Path) -> tuple[float, float] | None:
    intervals = _cover_reference_intervals(job_dir)
    return intervals[0][:2] if intervals else None


def _cover_reference_intervals(
    job_dir: Path,
    *,
    limit: int = 3,
) -> list[tuple[float, float, float]]:
    highlights_payload = read_json_file(job_dir / "highlights.json") or {}
    cuts = read_json_file(job_dir / "cuts.json") or {}
    semantic = highlights_payload.get("highlights")
    if not isinstance(semantic, list) or not semantic:
        semantic = cuts.get("semantic_highlights")
    if isinstance(semantic, list):
        ranked_semantic = sorted(
            [item for item in semantic if isinstance(item, dict)],
            key=lambda item: _cover_score(item.get("score")),
            reverse=True,
        )
        intervals = []
        for item in ranked_semantic:
            start = _cover_score(item.get("start"))
            end = _cover_score(item.get("end"))
            if start >= 0 and end > start:
                if any(start < existing_end and end > existing_start for existing_start, existing_end, _ in intervals):
                    continue
                intervals.append((start, end, _cover_score(item.get("score"))))
                if len(intervals) >= limit:
                    return intervals
        if intervals:
            return intervals

    raw_clips = cuts.get("clips") if isinstance(cuts.get("clips"), list) else []
    ranked_clips = sorted(
        [
            item for item in raw_clips
            if isinstance(item, dict) and item.get("keep", True) is not False
        ],
        key=lambda item: _cover_score(
            item.get("final_score")
            if item.get("final_score") is not None
            else item.get("content_score")
        ),
        reverse=True,
    )
    intervals = []
    for clip in ranked_clips:
        start = _cover_score(clip.get("start"))
        end = _cover_score(clip.get("end"))
        if start >= 0 and end - start >= 3.0:
            score = _cover_score(
                clip.get("final_score")
                if clip.get("final_score") is not None
                else clip.get("content_score")
            )
            if any(start < existing_end and end > existing_start for existing_start, existing_end, _ in intervals):
                continue
            intervals.append((start, end, score))
            if len(intervals) >= limit:
                break
    return intervals


def _image_data_url(path: Path) -> str:
    if path.stat().st_size > MAX_REMOTE_COVER_IMAGE_BYTES:
        raise RuntimeError("cover reference image is too large")
    media_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "application/octet-stream")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _thumbnail_summary(job_dir: Path) -> str:
    highlight_thumbnail = job_dir / "highlight_thumbnail.jpg"
    thumbnail = highlight_thumbnail if highlight_thumbnail.is_file() else job_dir / "thumbnail.jpg"
    if not thumbnail.is_file():
        return "no thumbnail available"
    try:
        from PIL import Image
        with Image.open(thumbnail) as image:
            sample = image.convert("RGB").resize((1, 1))
            red, green, blue = sample.getpixel((0, 0))
    except Exception:
        return "thumbnail exists"
    brightness = (red + green + blue) / 3
    if red > blue + 24 and red > green + 12:
        tone = "warm"
    elif blue > red + 24:
        tone = "cool"
    elif brightness < 80:
        tone = "dark"
    elif brightness > 180:
        tone = "bright"
    else:
        tone = "balanced"
    return f"{tone} frame, average rgb {red},{green},{blue}"


def _default_title(job_dir: Path) -> str:
    manifest = read_json_file(job_dir / "manifest.json") or {}
    source = str(manifest.get("source_name") or job_dir.name)
    return Path(source).stem.replace("_", " ").replace("-", " ").strip() or job_dir.name


def _cover_count(value: int) -> int:
    return 5 if int(value or 3) >= 5 else 3


def _cover_aspects(values: list[str]) -> list[str]:
    normalized = [value for value in values if value in ASPECT_SPECS]
    return normalized or ["9:16", "16:9"]


def _selected_from_existing(job_dir: Path) -> dict[str, str]:
    selected = {}
    for aspect, spec in ASPECT_SPECS.items():
        filename = str(spec["selected"])
        if (job_dir / filename).is_file():
            selected[aspect] = filename
    return selected


def _copy_file_atomic(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(f".{target.name}.{os.getpid()}.{int(time.time() * 1000)}.tmp")
    shutil.copyfile(source, tmp_path)
    os.replace(tmp_path, target)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
