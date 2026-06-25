from typing import Any, List, Dict
import logging

logger = logging.getLogger(__name__)

try:
    import video_automation_native
    cuts = video_automation_native.cuts
except ImportError:
    cuts = None

def generate_and_stabilize_clips(
    duration: float,
    invalid_segments: List[Dict[str, Any]],
    min_gap: float,
    min_clip_seconds: float,
    merge_gap_seconds: float,
) -> List[Dict[str, Any]]:
    if cuts is None:
        raise RuntimeError("video_automation_native.cuts is not available")
    return cuts.generate_and_stabilize_clips(
        duration, invalid_segments, min_gap, min_clip_seconds, merge_gap_seconds
    )

def attach_transcript_and_score(
    clips: List[Dict[str, Any]],
    transcript_segments: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    if cuts is None:
        raise RuntimeError("video_automation_native.cuts is not available")
    return cuts.attach_transcript_and_score(clips, transcript_segments)

def merge_invalid_ranges(
    duration: float,
    silences: List[Dict[str, Any]],
    freezes: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    if cuts is None:
        raise RuntimeError("video_automation_native.cuts is not available")
    return cuts.merge_invalid_ranges(duration, silences, freezes)
