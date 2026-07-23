from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from video_automation.subtitles import (
    _ass_time,
    _escape_ass_text,
    _remap_segments_to_clips,
    _subtitle_events_for_segment,
    _visual_width,
    _wrap_subtitle_text,
    play_resolution,
    prepare_subtitle_segments,
)


def _settings(**overrides: object) -> SimpleNamespace:
    values = {
        "subtitle_min_duration_seconds": 0.3,
        "subtitle_replacements": (("wrong", "right"),),
        "profanity_words": ("badword",),
        "subtitle_censor_replacement": "[beep]",
        "ass_font_name": "Arial",
        "ass_font_size": 56,
        "ass_primary_color": "&H00FFFFFF",
        "ass_outline_color": "&H00000000",
        "ass_back_color": "&H64000000",
        "ass_alignment": 2,
        "ass_margin_v": 90,
        "ass_outline": 3,
        "ass_shadow": 1,
        "ass_max_lines": 2,
        "ass_vertical_font_size": 44,
        "ass_preset": "classic",
        "subtitle_music_vocal_filter_enabled": True,
        "subtitle_music_vocal_min_duration_seconds": 1.5,
        "subtitle_music_vocal_min_chars_per_second": 1.8,
        "subtitle_music_vocal_min_avg_probability": 0.0,
        "subtitle_music_vocal_patterns": (),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class SubtitleTests(unittest.TestCase):
    def test_prepare_subtitle_segments_filters_replaces_and_censors(self) -> None:
        segments = [
            {"start": 0.0, "end": 0.1, "text": "too short"},
            {"start": 1.0, "end": 2.0, "text": "wrong badword"},
        ]
        prepared = prepare_subtitle_segments(_settings(), segments)  # type: ignore[arg-type]
        self.assertEqual(prepared, [{"start": 1.0, "end": 2.0, "text": "right [beep]"}])

    def test_remap_segments_to_clips_moves_to_new_timeline(self) -> None:
        segments = [{"start": 10.0, "end": 15.0, "text": "hello"}, {"start": 20.0, "end": 22.0, "text": "tail"}]
        clips = [{"start": 12.0, "end": 14.0, "duration": 2.0}, {"start": 20.0, "end": 21.0, "duration": 1.0}]
        self.assertEqual(
            _remap_segments_to_clips(segments, clips),
            [
                {"start": 0.0, "end": 2.0, "text": "hello"},
                {"start": 2.0, "end": 3.0, "text": "tail"},
            ],
        )

    def test_ass_helpers_escape_and_format(self) -> None:
        self.assertEqual(_ass_time(62.345), "0:01:02.34")
        self.assertEqual(_escape_ass_text("a{b}\\c\nd"), r"a\{b\}\\c\Nd")

    def test_visual_width_and_wrapping_handle_mixed_text(self) -> None:
        self.assertEqual(_visual_width("abc"), 3)
        self.assertEqual(_visual_width("\u4f60\u597d"), 4)
        wrapped = _wrap_subtitle_text("hello world this is a subtitle line", {"font_size": 80}, 480, 2)
        self.assertIn("\n", wrapped)

    def test_long_asr_segment_is_split_without_exceeding_line_limit(self) -> None:
        source = ("这是一个很长的语音转写片段，需要拆成多条字幕事件，" * 12) + r"\N还包含后端传入的换行命令。"
        events = _subtitle_events_for_segment(
            {"start": 0.0, "end": 24.0, "text": source},
            {"font_size": 52},
            1080,
            2,
        )

        self.assertGreater(len(events), 1)
        self.assertTrue(all(len(event["text"].splitlines()) <= 2 for event in events))
        actual = "".join("".join(event["text"].split()) for event in events)
        expected = "".join(source.replace(r"\N", " ").split())
        self.assertEqual(actual, expected)
        self.assertEqual(events[0]["start"], 0.0)
        self.assertEqual(events[-1]["end"], 24.0)

    def test_play_resolution_prefers_crop_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            (job_dir / "crop_plan.json").write_text('{"target":{"width":1080,"height":1920}}', encoding="utf-8")
            self.assertEqual(play_resolution(job_dir), (1080, 1920))


if __name__ == "__main__":
    unittest.main()
