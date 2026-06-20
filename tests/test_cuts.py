from __future__ import annotations

import unittest

from video_automation.cuts import (
    _attach_transcript_to_clips,
    _clips_from_invalid_segments,
    _validate_editor_clips,
    build_invalid_segments,
)


class CutPlanningTests(unittest.TestCase):
    def test_build_invalid_segments_prefers_silence_freeze_overlap(self) -> None:
        payload = build_invalid_segments(
            20.0,
            {"silences": [{"start": 2, "end": 8}, {"start": 12, "end": 15}]},
            {"freezes": [{"start": 5, "end": 10}, {"start": 14, "end": 18}]},
        )
        self.assertEqual(
            payload,
            [
                {"start": 5.0, "end": 8.0, "duration": 3.0, "drop": True, "reason": "silence+freeze"},
                {"start": 14.0, "end": 15.0, "duration": 1.0, "drop": True, "reason": "silence+freeze"},
            ],
        )

    def test_clips_from_invalid_segments_absorbs_fragmented_clips(self) -> None:
        clips = _clips_from_invalid_segments(
            12.0,
            [
                {"start": 3.0, "end": 3.4, "reason": "silence"},
                {"start": 6.0, "end": 6.4, "reason": "silence"},
            ],
            0.0,
            min_clip_seconds=2.0,
            merge_gap_seconds=1.0,
        )
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0]["start"], 0.0)
        self.assertEqual(clips[0]["end"], 12.0)
        self.assertGreaterEqual(clips[0]["duration"], 2.0)

    def test_attach_transcript_prefers_word_timestamps_within_clip(self) -> None:
        clips = [{"start": 1.0, "end": 3.0, "duration": 2.0, "keep": True, "reason": "test"}]
        segments = [{
            "start": 0.0,
            "end": 4.0,
            "text": "fallback text",
            "words": [
                {"start": 0.2, "end": 0.8, "word": "before"},
                {"start": 1.2, "end": 1.6, "word": "inside"},
                {"start": 3.2, "end": 3.6, "word": "after"},
            ],
        }]
        enriched = _attach_transcript_to_clips(clips, segments)
        self.assertEqual(enriched[0]["transcript_text"], "inside")

    def test_validate_editor_clips_clamps_and_sorts(self) -> None:
        clips = _validate_editor_clips(
            [
                {"start": "4", "end": "9", "reason": "later"},
                {"start": "-1", "end": "2", "reason": "first"},
            ],
            duration=5.0,
        )
        self.assertEqual([(clip["start"], clip["end"]) for clip in clips], [(0.0, 2.0), (4.0, 5.0)])


if __name__ == "__main__":
    unittest.main()
