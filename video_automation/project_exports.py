from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .io_utils import read_json_file, write_json_atomic, write_text_atomic
from .profanity import apply_replacements, censor_text

CLIP_EXPORT_LIMIT = 100


def generate_project_exports(
    settings: Settings,
    job_dir: Path,
    *,
    targets: list[str] | None = None,
    include_clips: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    selected = _normalize_targets(targets)
    manifest_path = job_dir / "project_export_manifest.json"
    if manifest_path.exists() and not force:
        cached = read_json_file(manifest_path)
        if isinstance(cached, dict) and _manifest_satisfies(cached, selected, include_clips):
            return cached

    cuts = read_json_file(job_dir / "cuts.json") or {}
    media_manifest = read_json_file(job_dir / "manifest.json") or {}
    clips = _kept_clips(cuts)
    if not clips:
        raise RuntimeError("cuts.json has no clips marked keep=true")

    output_root = job_dir / "project_exports"
    output_root.mkdir(parents=True, exist_ok=True)
    exports: dict[str, Any] = {}
    errors: list[dict[str, str]] = []

    if "premiere" in selected:
        try:
            exports["premiere"] = _export_premiere(settings, job_dir, output_root, media_manifest, clips, force=force)
        except Exception as exc:
            errors.append({"target": "premiere", "error": str(exc)})

    if "jianying" in selected:
        try:
            exports["jianying"] = _export_jianying(settings, job_dir, output_root, media_manifest, clips, include_clips=include_clips, force=force)
        except Exception as exc:
            errors.append({"target": "jianying", "error": str(exc)})

    payload = {
        "status": "ready" if exports and not errors else "partial" if exports else "failed",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "targets": selected,
        "include_clips": include_clips,
        "source_media": _source_summary(job_dir, media_manifest),
        "exports": exports,
        "errors": errors,
        "notes": [
            "Premiere export uses Final Cut Pro 7 XML for direct import.",
            "Jianying export is a stable handoff package, not a proprietary draft project.",
        ],
    }
    write_json_atomic(manifest_path, payload)
    if not exports:
        raise RuntimeError("; ".join(item["error"] for item in errors) or "project export failed")
    return payload


def _export_premiere(
    settings: Settings,
    job_dir: Path,
    output_root: Path,
    media_manifest: dict[str, Any],
    clips: list[dict[str, float]],
    *,
    force: bool,
) -> dict[str, Any]:
    target_dir = output_root / "premiere"
    target_dir.mkdir(parents=True, exist_ok=True)
    timeline_path = target_dir / "premiere_timeline.xml"
    subtitles_path = target_dir / "premiere_subtitles.srt"
    readme_path = target_dir / "README_IMPORT.txt"
    source, source_mode, timeline_clips = _premiere_source_and_clips(job_dir, media_manifest, clips)
    fps = _fps(media_manifest)
    width = _manifest_dimension(media_manifest, "width") or 1920
    height = _manifest_dimension(media_manifest, "height") or 1080
    duration = _duration_for_source(job_dir, media_manifest, source, timeline_clips)

    if force or not timeline_path.exists():
        _write_fcp7_xml(timeline_path, source, source_mode, timeline_clips, fps=fps, width=width, height=height, duration=duration)
    if force or not subtitles_path.exists():
        _write_clipped_srt(settings, job_dir, subtitles_path, timeline_clips if source_mode == "original" else [{"start": 0.0, "end": duration, "duration": duration}])
    if force or not readme_path.exists():
        write_text_atomic(readme_path, _premiere_readme(source, source_mode))

    files = [_entry(job_dir, timeline_path), _entry(job_dir, subtitles_path), _entry(job_dir, readme_path)]
    return {
        "status": "ready",
        "format": "final_cut_pro_7_xml",
        "source_mode": source_mode,
        "clip_count": len(timeline_clips),
        "files": files,
        "import_note": "Premiere Pro: File > Import, then select premiere_timeline.xml. Import the SRT separately if needed.",
    }


def _export_jianying(
    settings: Settings,
    job_dir: Path,
    output_root: Path,
    media_manifest: dict[str, Any],
    clips: list[dict[str, float]],
    *,
    include_clips: bool,
    force: bool,
) -> dict[str, Any]:
    target_dir = output_root / "jianying_package"
    target_dir.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, Any]] = []
    for name in ["final.mp4", "review.mp4", "cover_vertical.jpg", "cover_landscape.jpg", "metadata.json"]:
        source = job_dir / name
        if source.exists() and source.is_file():
            copied.append(_copy_file(job_dir, source, target_dir / name, force=force))

    subtitles_path = target_dir / "subtitles.srt"
    if force or not subtitles_path.exists():
        _write_clipped_srt(settings, job_dir, subtitles_path, clips)
    copied.append(_entry(job_dir, subtitles_path))

    clips_json = target_dir / "clips.json"
    write_json_atomic(clips_json, {"clips": clips, "source": "cuts.json"})
    copied.append(_entry(job_dir, clips_json))

    clip_files: list[dict[str, Any]] = []
    if include_clips:
        source = _original_source(media_manifest)
        if source and source.exists():
            clips_dir = target_dir / "clips"
            clips_dir.mkdir(parents=True, exist_ok=True)
            for index, clip in enumerate(clips[:CLIP_EXPORT_LIMIT], start=1):
                output = clips_dir / f"clip_{index:03d}.mp4"
                if force or not output.exists() or output.stat().st_size == 0:
                    _copy_segment(settings, source, output, float(clip["start"]), float(clip["duration"]))
                clip_files.append(_entry(job_dir, output))

    readme_path = target_dir / "README_IMPORT.txt"
    write_text_atomic(readme_path, _jianying_readme(include_clips=include_clips, clip_count=len(clip_files)))
    copied.append(_entry(job_dir, readme_path))

    return {
        "status": "ready",
        "format": "handoff_package",
        "include_clips": include_clips,
        "clip_export_limit": CLIP_EXPORT_LIMIT,
        "clip_export_truncated": include_clips and len(clips) > CLIP_EXPORT_LIMIT,
        "files": copied,
        "clips": clip_files,
        "import_note": "Import the video and subtitles.srt into Jianying/CapCut manually. This is not a draft project.",
    }


def _write_fcp7_xml(
    output_path: Path,
    source: Path,
    source_mode: str,
    clips: list[dict[str, float]],
    *,
    fps: float,
    width: int,
    height: int,
    duration: float,
) -> None:
    timebase, ntsc = _timebase(fps)
    source_duration_frames = _frames(duration, timebase)
    timeline_duration_frames = sum(_frames(float(clip["duration"]), timebase) for clip in clips)
    xmeml = ET.Element("xmeml", {"version": "4"})
    sequence = ET.SubElement(xmeml, "sequence", {"id": "sequence-1"})
    ET.SubElement(sequence, "name").text = f"{source.stem} - Video Automation"
    _rate(sequence, timebase, ntsc)
    ET.SubElement(sequence, "duration").text = str(timeline_duration_frames)
    media = ET.SubElement(sequence, "media")
    video = ET.SubElement(media, "video")
    track = ET.SubElement(video, "track")
    audio = ET.SubElement(media, "audio")
    audio_track = ET.SubElement(audio, "track")

    cursor = 0
    for index, clip in enumerate(clips, start=1):
        clip_frames = _frames(float(clip["duration"]), timebase)
        in_frame = _frames(float(clip["start"]), timebase) if source_mode == "original" else 0
        out_frame = in_frame + clip_frames
        video_id = f"clipitem-v{index}"
        audio_id = f"clipitem-a{index}"
        _clipitem(
            track,
            video_id,
            source,
            index,
            cursor,
            cursor + clip_frames,
            in_frame,
            out_frame,
            source_duration_frames,
            timebase,
            ntsc,
            width,
            height,
            kind="video",
            linked_id=audio_id,
            full_file_definition=index == 1,
        )
        _clipitem(
            audio_track,
            audio_id,
            source,
            index,
            cursor,
            cursor + clip_frames,
            in_frame,
            out_frame,
            source_duration_frames,
            timebase,
            ntsc,
            width,
            height,
            kind="audio",
            linked_id=video_id,
            full_file_definition=False,
        )
        cursor += clip_frames

    tree = ET.ElementTree(xmeml)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    tree.write(tmp_path, encoding="utf-8", xml_declaration=True)
    text = tmp_path.read_text(encoding="utf-8")
    tmp_path.write_text(text.replace("?>", "?>\n<!DOCTYPE xmeml>", 1), encoding="utf-8", newline="\n")
    tmp_path.replace(output_path)


def _clipitem(
    parent: ET.Element,
    item_id: str,
    source: Path,
    index: int,
    start: int,
    end: int,
    in_frame: int,
    out_frame: int,
    source_duration_frames: int,
    timebase: int,
    ntsc: bool,
    width: int,
    height: int,
    *,
    kind: str,
    linked_id: str,
    full_file_definition: bool,
) -> None:
    item = ET.SubElement(parent, "clipitem", {"id": item_id})
    ET.SubElement(item, "name").text = f"{source.name} #{index:03d}"
    ET.SubElement(item, "duration").text = str(max(1, end - start))
    _rate(item, timebase, ntsc)
    ET.SubElement(item, "start").text = str(start)
    ET.SubElement(item, "end").text = str(end)
    ET.SubElement(item, "in").text = str(in_frame)
    ET.SubElement(item, "out").text = str(out_frame)
    file_node = ET.SubElement(item, "file", {"id": "file-1"})
    if not full_file_definition:
        _links(item, item_id, linked_id, kind, index)
        return
    ET.SubElement(file_node, "name").text = source.name
    ET.SubElement(file_node, "pathurl").text = _path_url(source)
    _rate(file_node, timebase, ntsc)
    ET.SubElement(file_node, "duration").text = str(source_duration_frames)
    media = ET.SubElement(file_node, "media")
    video = ET.SubElement(media, "video")
    sample = ET.SubElement(video, "samplecharacteristics")
    _rate(sample, timebase, ntsc)
    ET.SubElement(sample, "width").text = str(width)
    ET.SubElement(sample, "height").text = str(height)
    audio = ET.SubElement(media, "audio")
    ET.SubElement(audio, "channelcount").text = "2"
    _links(item, item_id, linked_id, kind, index)


def _links(item: ET.Element, item_id: str, linked_id: str, kind: str, index: int) -> None:
    link = ET.SubElement(item, "link")
    ET.SubElement(link, "linkclipref").text = item_id
    ET.SubElement(link, "mediatype").text = kind
    ET.SubElement(link, "trackindex").text = "1"
    ET.SubElement(link, "clipindex").text = str(index)
    other = ET.SubElement(item, "link")
    ET.SubElement(other, "linkclipref").text = linked_id
    ET.SubElement(other, "mediatype").text = "audio" if kind == "video" else "video"
    ET.SubElement(other, "trackindex").text = "1"
    ET.SubElement(other, "clipindex").text = str(index)


def _write_clipped_srt(settings: Settings, job_dir: Path, output_path: Path, clips: list[dict[str, float]]) -> None:
    transcript = read_json_file(job_dir / "transcript.json") or {}
    segments = _transcript_segments(transcript)
    remapped = _remap_segments_to_clips(segments, clips)
    lines: list[str] = []
    for index, segment in enumerate(remapped, start=1):
        text = apply_replacements(str(segment["text"]), settings.subtitle_replacements)
        text = censor_text(text, settings.profanity_words, replacement=settings.subtitle_censor_replacement).strip()
        if not text:
            continue
        lines.extend([str(index), f"{_srt_time(segment['start'])} --> {_srt_time(segment['end'])}", text, ""])
    write_text_atomic(output_path, "\n".join(lines))


def _transcript_segments(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    segments = []
    for raw in transcript.get("segments", []):
        if not isinstance(raw, dict):
            continue
        try:
            start = float(raw["start"])
            end = float(raw["end"])
        except (KeyError, TypeError, ValueError):
            continue
        text = str(raw.get("text") or "").strip()
        if end > start and text:
            segments.append({"start": start, "end": end, "text": text})
    return segments


def _remap_segments_to_clips(segments: list[dict[str, Any]], clips: list[dict[str, float]]) -> list[dict[str, Any]]:
    remapped = []
    offset = 0.0
    for clip in clips:
        clip_start = float(clip["start"])
        clip_end = float(clip["end"])
        if clip.get("subtitle_override"):
            text = str(clip.get("subtitle_text") or "").strip()
            if text:
                remapped.append({"start": round(offset, 3), "end": round(offset + float(clip["duration"]), 3), "text": text})
            offset += float(clip["duration"])
            continue
        for segment in segments:
            overlap_start = max(float(segment["start"]), clip_start)
            overlap_end = min(float(segment["end"]), clip_end)
            if overlap_end <= overlap_start:
                continue
            remapped.append({
                "start": round(offset + (overlap_start - clip_start), 3),
                "end": round(offset + (overlap_end - clip_start), 3),
                "text": segment["text"],
            })
        offset += float(clip["duration"])
    return remapped


def _copy_segment(settings: Settings, source: Path, output: Path, start: float, duration: float) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([
        str(settings.ffmpeg_path),
        "-hide_banner",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(source),
        "-t",
        f"{duration:.3f}",
        "-map",
        "0",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output),
    ], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg clip export failed: {result.stderr.strip()}")


def _premiere_source_and_clips(job_dir: Path, manifest: dict[str, Any], clips: list[dict[str, float]]) -> tuple[Path, str, list[dict[str, float]]]:
    original = _original_source(manifest)
    if original and original.exists():
        return original, "original", clips
    fallback = _preferred_rendered_video(job_dir)
    if fallback is None:
        raise RuntimeError("source media is missing and final.mp4/review.mp4 is not available")
    duration = _duration_for_rendered(job_dir, fallback)
    if duration <= 0:
        duration = sum(float(clip.get("duration") or 0.0) for clip in clips)
    return fallback, "rendered_fallback", [{"start": 0.0, "end": duration, "duration": duration, "keep": True}]


def _original_source(manifest: dict[str, Any]) -> Path | None:
    value = str(manifest.get("source_path") or "").strip()
    if not value:
        return None
    return Path(value)


def _preferred_rendered_video(job_dir: Path) -> Path | None:
    for name in ["final.mp4", "review.mp4"]:
        path = job_dir / name
        if path.exists() and path.stat().st_size > 0:
            return path
    return None


def _duration_for_source(job_dir: Path, manifest: dict[str, Any], source: Path, clips: list[dict[str, float]]) -> float:
    original = _original_source(manifest)
    if original and source.resolve() == original.resolve():
        return float(manifest.get("duration_seconds") or max((clip["end"] for clip in clips), default=0.0))
    return _duration_for_rendered(job_dir, source)


def _duration_for_rendered(job_dir: Path, source: Path) -> float:
    for name in ["final_render_preview.json", "render_preview.json"]:
        preview = read_json_file(job_dir / name) or {}
        if str(preview.get("output_path") or "").lower() == str(source).lower():
            try:
                return sum(float(clip.get("duration") or 0.0) for clip in preview.get("clips", []))
            except (TypeError, ValueError):
                pass
    return 0.0


def _kept_clips(cuts: dict[str, Any]) -> list[dict[str, float]]:
    clips = []
    for raw in cuts.get("clips", []):
        if not isinstance(raw, dict) or not raw.get("keep", True):
            continue
        try:
            start = float(raw["start"])
            end = float(raw["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            continue
        clip = dict(raw)
        clip["start"] = round(start, 3)
        clip["end"] = round(end, 3)
        clip["duration"] = round(end - start, 3)
        clips.append(clip)
    return sorted(clips, key=lambda item: item["start"])


def _normalize_targets(targets: list[str] | None) -> list[str]:
    allowed = {"premiere", "jianying"}
    values = []
    for item in targets or ["premiere", "jianying"]:
        value = str(item).strip().lower()
        if value not in allowed:
            raise RuntimeError(f"unsupported project export target: {value}")
        if value not in values:
            values.append(value)
    return values or ["premiere", "jianying"]


def _manifest_satisfies(manifest: dict[str, Any], targets: list[str], include_clips: bool) -> bool:
    if any(target not in (manifest.get("exports") or {}) for target in targets):
        return False
    if include_clips and not manifest.get("include_clips"):
        return False
    return True


def _source_summary(job_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    source = _original_source(manifest)
    fallback = _preferred_rendered_video(job_dir)
    return {
        "original_path": str(source) if source else "",
        "original_exists": bool(source and source.exists()),
        "rendered_fallback": str(fallback) if fallback else "",
    }


def _copy_file(job_dir: Path, source: Path, target: Path, *, force: bool) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    if force or not target.exists() or target.stat().st_size != source.stat().st_size:
        shutil.copy2(source, target)
    return _entry(job_dir, target)


def _entry(job_dir: Path, path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "relative_path": str(path.relative_to(job_dir)).replace("\\", "/"),
        "size_bytes": stat.st_size,
        "modified_at": int(stat.st_mtime),
    }


def _fps(manifest: dict[str, Any]) -> float:
    try:
        value = float(manifest.get("fps") or 30.0)
    except (TypeError, ValueError):
        return 30.0
    return value if value > 0 else 30.0


def _timebase(fps: float) -> tuple[int, bool]:
    ntsc_rates = {23.976: 24, 29.97: 30, 59.94: 60}
    for ntsc_fps, timebase in ntsc_rates.items():
        if abs(fps - ntsc_fps) < 0.05:
            return timebase, True
    return max(1, int(round(fps))), False


def _frames(seconds: float, timebase: int) -> int:
    return max(1, int(round(max(0.0, seconds) * timebase)))


def _rate(parent: ET.Element, timebase: int, ntsc: bool) -> None:
    rate = ET.SubElement(parent, "rate")
    ET.SubElement(rate, "timebase").text = str(timebase)
    ET.SubElement(rate, "ntsc").text = "TRUE" if ntsc else "FALSE"


def _path_url(path: Path) -> str:
    try:
        return path.resolve().as_uri()
    except ValueError:
        return "file://localhost/" + str(path).replace("\\", "/")


def _positive_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _manifest_dimension(manifest: dict[str, Any], key: str) -> int:
    direct = _positive_int(manifest.get(key))
    if direct:
        return direct
    for stream in manifest.get("streams", []):
        if isinstance(stream, dict) and stream.get("codec_type") == "video":
            value = _positive_int(stream.get(key))
            if value:
                return value
    return 0


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    ms = milliseconds % 1000
    total_seconds = milliseconds // 1000
    sec = total_seconds % 60
    total_minutes = total_seconds // 60
    minute = total_minutes % 60
    hour = total_minutes // 60
    return f"{hour:02d}:{minute:02d}:{sec:02d},{ms:03d}"


def _premiere_readme(source: Path, source_mode: str) -> str:
    return "\n".join([
        "Premiere Pro import",
        "",
        "1. Open Premiere Pro.",
        "2. Use File > Import and select premiere_timeline.xml.",
        "3. If subtitles are needed, import premiere_subtitles.srt separately.",
        "",
        f"Source mode: {source_mode}",
        f"Media path: {source}",
        "",
        "If Premiere asks to relink media, choose the original video file.",
        "",
    ])


def _jianying_readme(*, include_clips: bool, clip_count: int) -> str:
    lines = [
        "Jianying / CapCut handoff package",
        "",
        "This folder is not a Jianying draft project. It contains stable media assets for manual import.",
        "",
        "Recommended import order:",
        "1. Import final.mp4 or review.mp4 as the main video.",
        "2. Import subtitles.srt as captions.",
        "3. Import cover_vertical.jpg or cover_landscape.jpg as the upload cover if available.",
        "4. Use metadata.json for title, description, tags, and cover text.",
    ]
    if include_clips:
        cap_note = f" Export is capped at {CLIP_EXPORT_LIMIT} clips." if clip_count >= CLIP_EXPORT_LIMIT else ""
        lines.extend(["", f"Clips exported: {clip_count}.{cap_note} You can import clips/*.mp4 for manual rearranging."])
    lines.append("")
    return "\n".join(lines)
