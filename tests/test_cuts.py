from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_automation.cuts import (
    _attach_transcript_to_clips,
    _clips_from_invalid_segments,
    _summarize_segments,
    _validate_editor_clips,
    build_invalid_segments,
    update_cuts_from_editor,
)
from video_automation import cuts
from video_automation.io_utils import write_json_atomic


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

    def test_transcript_summary_bounds_text_and_word_payloads(self) -> None:
        payload = _summarize_segments({"segments": [{
            "start": 0,
            "end": 1,
            "text": "x" * 5000,
            "words": [{"start": 0, "end": 1, "word": "y" * 200} for _ in range(300)],
        }]})

        self.assertLessEqual(len(payload[0]["text"]), 4096)
        self.assertEqual(len(payload[0]["words"]), 200)
        self.assertLessEqual(len(payload[0]["words"][0]["word"]), 128)

    def test_validate_editor_clips_clamps_and_sorts(self) -> None:
        clips = _validate_editor_clips(
            [
                {"start": "4", "end": "9", "reason": "later"},
                {"start": "-1", "end": "2", "reason": "first"},
            ],
            duration=5.0,
        )
        self.assertEqual([(clip["start"], clip["end"]) for clip in clips], [(0.0, 2.0), (4.0, 5.0)])

    def test_manual_cut_edit_invalidates_automated_refinement_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_json_atomic(
                job_dir / "cuts.json",
                {
                    "source": "source.mp4",
                    "duration_seconds": 5.0,
                    "invalid_segments": [],
                    "highlight_signals": {"scene_count": 0, "scenes": []},
                    "transcript_segments": [],
                    "clips": [
                        {
                            "start": 0.0,
                            "end": 5.0,
                            "duration": 5.0,
                            "keep": True,
                            "reason": "tail",
                        }
                    ],
                    "notes": [],
                    "refinement": {"status": "accepted"},
                },
            )
            (job_dir / "clip_refinement.json").write_text("{}", encoding="utf-8")

            settings_stub = type(
                "SettingsStub",
                (),
                {"native_cuts_enabled": False},
            )()
            with patch.object(cuts.Settings, "load", return_value=settings_stub):
                payload = update_cuts_from_editor(
                    job_dir,
                    [{"start": 0.0, "end": 4.0, "keep": True, "reason": "manual"}],
                )

            self.assertFalse((job_dir / "clip_refinement.json").exists())
            self.assertNotIn("refinement", payload)


class NativeCutsTests(unittest.TestCase):
    def test_build_invalid_segments_uses_native_when_enabled(self) -> None:
        silence_payload = {"silences": [{"start": 2, "end": 8}]}
        freeze_payload = {"freezes": [{"start": 5, "end": 10}]}
        with patch.object(cuts.native_cuts, "merge_invalid_ranges", return_value=[{"fake": "data"}]) as native:
            payload = cuts.build_invalid_segments(20.0, silence_payload, freeze_payload, native_enabled=True)
            native.assert_called_once_with(20.0, silence_payload["silences"], freeze_payload["freezes"])
            self.assertEqual(payload, [{"fake": "data"}])

    def test_build_invalid_segments_skips_native_when_disabled(self) -> None:
        silence_payload = {"silences": [{"start": 2, "end": 8}]}
        freeze_payload = {"freezes": [{"start": 5, "end": 10}]}
        with patch.object(cuts.native_cuts, "merge_invalid_ranges") as native:
            payload = cuts.build_invalid_segments(20.0, silence_payload, freeze_payload, native_enabled=False)
            native.assert_not_called()
            self.assertEqual(len(payload), 1)

    def test_native_cuts_matches_python_fallback(self) -> None:
        try:
            import video_automation_native  # noqa: F401
        except ImportError:
            self.skipTest("optional video_automation_native extension is not installed")

        silence_payload = {"silences": [{"start": 2, "end": 8}, {"start": 12, "end": 15}]}
        freeze_payload = {"freezes": [{"start": 5, "end": 10}, {"start": 14, "end": 18}]}

        python_res = cuts.build_invalid_segments(20.0, silence_payload, freeze_payload, native_enabled=False)
        native_res = cuts.build_invalid_segments(20.0, silence_payload, freeze_payload, native_enabled=True)

        self.assertEqual(python_res, native_res)

    def test_native_clip_generation_preserves_python_reason_metadata(self) -> None:
        try:
            import video_automation_native  # noqa: F401
        except ImportError:
            self.skipTest("optional video_automation_native extension is not installed")

        invalid_segments = [
            {"start": 122.028, "end": 122.938, "duration": 0.91, "drop": True, "reason": "silence+freeze"},
            {"start": 149.628, "end": 167.958, "duration": 18.33, "drop": True, "reason": "silence+freeze"},
        ]

        python_clips = cuts._clips_from_invalid_segments(
            170.0,
            invalid_segments,
            0.35,
            min_clip_seconds=2.0,
            merge_gap_seconds=1.5,
        )
        native_clips = cuts.native_cuts.generate_and_stabilize_clips(
            170.0,
            invalid_segments,
            0.35,
            2.0,
            1.5,
        )

        self.assertEqual(native_clips, python_clips)


if __name__ == "__main__":
    unittest.main()
