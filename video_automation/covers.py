from __future__ import annotations

import base64
import json
import os
import shutil
import textwrap
import time
import urllib.error
import urllib.request
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from .config import Settings
from .io_utils import read_json_file, write_json_atomic


ASPECT_SPECS = {
    "9:16": {"slug": "9x16", "selected": "cover_vertical.jpg", "size": "1024x1536", "final": (1080, 1920)},
    "16:9": {"slug": "16x9", "selected": "cover_landscape.jpg", "size": "1536x1024", "final": (1920, 1080)},
}
STYLE_PROMPTS = {
    "short_video": "bold Chinese short-video cover, strong subject, high contrast, vivid but clean",
    "clean": "minimal clean editorial cover, modern layout, clear subject, premium calm lighting",
    "cinematic": "cinematic poster-like cover, dramatic lighting, film still mood, premium composition",
    "gaming": "energetic gaming livestream cover, esports style, dynamic lighting, high-impact composition",
}


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
    title = str(options.get("title") or _default_title(job_dir)).strip()
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
    if settings.cover_provider.strip().lower() != "openai":
        raise RuntimeError(f"unsupported COVER_PROVIDER: {settings.cover_provider}")
    if not settings.openai_api_key.strip():
        raise RuntimeError("OPENAI_API_KEY is not configured")

    normalized_count = _cover_count(count if count is not None else settings.cover_count)
    normalized_aspects = _cover_aspects(aspects or list(settings.cover_aspects))
    normalized_style = style if style in STYLE_PROMPTS else "short_video"
    manifest_path = job_dir / "cover_manifest.json"
    prompt_title = (title or _default_title(job_dir)).strip()
    context = _cover_context(job_dir, prompt_title)
    manifest = _initial_manifest(
        settings,
        job_dir,
        title=prompt_title,
        style=normalized_style,
        count=normalized_count,
        aspects=normalized_aspects,
    )
    write_json_atomic(manifest_path, manifest)

    try:
        for aspect in normalized_aspects:
            spec = ASPECT_SPECS[aspect]
            prompt = _build_prompt(context, aspect, normalized_style)
            payload = _openai_generate_images(settings, prompt, normalized_count, spec["size"])
            candidates = []
            for index, item in enumerate(payload.get("data") or [], start=1):
                raw = item.get("b64_json")
                if not raw:
                    continue
                filename = f"cover_{spec['slug']}_{index:02}.jpg"
                output_path = job_dir / filename
                _postprocess_cover(
                    base64.b64decode(raw),
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
        manifest["error"] = ""
        write_json_atomic(manifest_path, manifest)
        return manifest
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["updated_at"] = _now()
        manifest["error"] = str(exc)
        write_json_atomic(manifest_path, manifest)
        raise


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


def _openai_generate_images(settings: Settings, prompt: str, count: int, size: str) -> dict[str, Any]:
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
        "https://api.openai.com/v1/images/generations",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.openai_api_key.strip()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            message = payload.get("error", {}).get("message") or payload.get("error") or str(exc)
        except Exception:
            message = str(exc)
        raise RuntimeError(f"OpenAI image generation failed: {message}") from exc


def _postprocess_cover(raw: bytes, output_path: Path, *, size: tuple[int, int], title: str, font_name: str, output_format: str) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("cover generation requires Pillow; install requirements-optional.txt") from exc

    with Image.open(BytesIO(raw)) as source:
        image = _cover_resize(source.convert("RGB"), size)
    if title:
        draw = ImageDraw.Draw(image, "RGBA")
        font = _cover_font(ImageFont, font_name, max(44, int(size[0] * 0.06)))
        lines = _wrap_title(draw, title, font, max_width=int(size[0] * 0.84))
        line_height = _text_height(draw, "测", font) + int(size[1] * 0.014)
        block_height = line_height * len(lines) + int(size[1] * 0.06)
        y0 = size[1] - block_height - int(size[1] * 0.045)
        draw.rounded_rectangle(
            [int(size[0] * 0.06), y0, int(size[0] * 0.94), size[1] - int(size[1] * 0.045)],
            radius=max(18, int(size[0] * 0.025)),
            fill=(0, 0, 0, 150),
        )
        y = y0 + int(size[1] * 0.03)
        for line in lines:
            width = _text_width(draw, line, font)
            x = (size[0] - width) / 2
            for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
                draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 210))
            draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
            y += line_height
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp")
    image.save(tmp_path, format="JPEG", quality=92)
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
        f"Create a polished video cover background for a creator upload. Aspect ratio: {aspect}. "
        f"Style: {style_prompt}. "
        "Do not include readable text, captions, logos, watermarks, UI, platform badges, or screenshots. "
        "Leave visual breathing room in the lower third for a title overlay. "
        f"Video title: {context['title']}. "
        f"Content summary: {context['summary']}. "
        f"Key moments: {context['highlights']}. "
        f"Thumbnail visual cue: {context['thumbnail']}."
    )


def _cover_context(job_dir: Path, title: str) -> dict[str, str]:
    manifest = read_json_file(job_dir / "manifest.json") or {}
    cuts = read_json_file(job_dir / "cuts.json") or {}
    transcript = read_json_file(job_dir / "transcript.json") or {}
    source_name = manifest.get("source_name") or job_dir.name
    segments = transcript.get("segments") if isinstance(transcript.get("segments"), list) else []
    transcript_text = " ".join(str(segment.get("text", "")) for segment in segments[:12] if isinstance(segment, dict))
    clips = cuts.get("clips") if isinstance(cuts.get("clips"), list) else []
    best = sorted(
        [clip for clip in clips if isinstance(clip, dict)],
        key=lambda clip: float(clip.get("content_score") or 0),
        reverse=True,
    )[:5]
    highlights = " / ".join(str(clip.get("transcript_text") or clip.get("reason") or "")[:80] for clip in best)
    return {
        "title": title or Path(str(source_name)).stem,
        "summary": (transcript_text or str(source_name))[:900],
        "highlights": (highlights or "important commentary moments from the video")[:600],
        "thumbnail": _thumbnail_summary(job_dir),
    }


def _thumbnail_summary(job_dir: Path) -> str:
    thumbnail = job_dir / "thumbnail.jpg"
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
