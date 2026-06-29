from __future__ import annotations

from dataclasses import replace
from typing import Any

from .config import Settings


PROFILE_PRESETS: dict[str, dict[str, Any]] = {
    "fast": {
        "detect_silence": True,
        "detect_freeze": False,
        "detect_scenes": True,
        "plan_crop": False,
        "render_review": False,
        "render_final": True,
        "vertical": False,
        "burn_subtitles": True,
    },
    "analysis": {
        "detect_silence": True,
        "detect_freeze": True,
        "detect_scenes": True,
        "plan_crop": True,
        "render_review": False,
        "render_final": False,
        "vertical": False,
        "burn_subtitles": False,
    },
    "douyin": {
        "detect_silence": True,
        "detect_freeze": True,
        "detect_scenes": True,
        "plan_crop": True,
        "render_review": False,
        "render_final": True,
        "vertical": True,
        "burn_subtitles": True,
    },
    "bilibili": {
        "detect_silence": True,
        "detect_freeze": True,
        "detect_scenes": True,
        "plan_crop": True,
        "render_review": False,
        "render_final": True,
        "vertical": False,
        "burn_subtitles": True,
    },
    "youtube_shorts": {
        "detect_silence": True,
        "detect_freeze": True,
        "detect_scenes": True,
        "plan_crop": True,
        "render_review": False,
        "render_final": True,
        "vertical": True,
        "burn_subtitles": True,
    },
}


def normalize_profile(profile: str | None) -> str:
    value = (profile or "").strip().lower().replace("-", "_")
    return value if value in PROFILE_PRESETS else ""


def profile_flags(profile: str | None) -> dict[str, bool]:
    value = normalize_profile(profile)
    return dict(PROFILE_PRESETS.get(value, {}))


def apply_profile_flags(options: dict[str, Any], profile: str | None) -> dict[str, Any]:
    merged = dict(options)
    names = {
        "detect_silence": "detect_silence_enabled",
        "detect_freeze": "detect_freeze_enabled",
        "detect_scenes": "detect_scenes_enabled",
        "render_review": "render_review_enabled",
        "render_final": "render_final_enabled",
        "vertical": "vertical_enabled",
        "burn_subtitles": "burn_subtitles_enabled",
        "plan_crop": "plan_crop_enabled",
        "plan_uvr": "plan_uvr_enabled",
    }
    for name, enabled in profile_flags(profile).items():
        if enabled:
            merged[names.get(name, name)] = True
    return merged


def apply_profile_settings(settings: Settings, profile: str | None) -> Settings:
    value = normalize_profile(profile)
    if not value:
        return settings
    if value == "fast":
        return replace(
            settings,
            source_integrity_scan_enabled=False,
            high_quality_audio_enabled=False,
            web_preview_enabled=False,
            render_x264_preset="veryfast",
            render_x264_crf=23,
        )
    updates: dict[str, Any] = {"export_platforms": (value,)}
    if value in {"douyin", "bilibili"}:
        updates["ass_preset"] = value
    return replace(settings, **updates)
