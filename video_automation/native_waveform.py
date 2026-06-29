from __future__ import annotations

from pathlib import Path
from typing import Any


class NativeWaveformUnavailable(RuntimeError):
    """Raised when the optional Rust waveform extension is not installed."""


def generate_waveform(audio_path: Path, *, pixels_per_second: int = 20) -> dict[str, Any]:
    try:
        import video_automation_native  # type: ignore[import-not-found]
    except ImportError as exc:
        raise NativeWaveformUnavailable("video_automation_native is not installed") from exc

    payload = video_automation_native.waveform_from_wav(str(audio_path), int(pixels_per_second))
    if not isinstance(payload, dict):
        raise RuntimeError("native waveform extension returned an invalid payload")
    return dict(payload)
