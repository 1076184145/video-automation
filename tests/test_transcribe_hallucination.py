"""Tests for the Whisper anti-hallucination parameter pack."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from video_automation import transcribe


def _settings(**overrides: object) -> SimpleNamespace:
    values = {
        "subtitle_replacements": (),
        "profanity_words": (),
        "subtitle_censor_replacement": "[beep]",
        "whisper_repetition_scrub_enabled": True,
        "whisper_no_speech_threshold": 0.6,
        "whisper_log_prob_threshold": -1.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class RepeatedPhraseCollapseTests(unittest.TestCase):
    def test_cjk_phrase_loop_collapsed_to_three(self) -> None:
        cleaned = transcribe._collapse_repeated_phrases("谢谢你" * 9)
        self.assertEqual(cleaned, "谢谢你" * 3)

    def test_punctuated_repeats_keep_separator(self) -> None:
        cleaned = transcribe._collapse_repeated_phrases("请不吝点赞，" * 6)
        self.assertEqual(cleaned, "请不吝点赞，" * 3)

    def test_english_phrase_loop_collapsed(self) -> None:
        cleaned = transcribe._collapse_repeated_phrases("Thank you for watching. " * 5)
        self.assertEqual(cleaned, "Thank you for watching. " * 3)

    def test_legitimate_text_untouched(self) -> None:
        text = "今天我们聊聊性能优化，明天再聊聊架构设计。"
        self.assertEqual(transcribe._collapse_repeated_phrases(text), text)

    def test_two_repeats_are_not_degeneration(self) -> None:
        text = "很好，很好，非常好。"
        self.assertEqual(transcribe._collapse_repeated_phrases(text), text)

    def test_postprocess_text_respects_toggle(self) -> None:
        text = "谢谢你" * 8
        enabled = transcribe._postprocess_text(text, _settings())
        disabled = transcribe._postprocess_text(
            text, _settings(whisper_repetition_scrub_enabled=False)
        )
        self.assertEqual(enabled, "谢谢你" * 3)
        self.assertEqual(disabled, text)


class RepeatedSegmentCollapseTests(unittest.TestCase):
    def _segment(self, index: int, text: str) -> dict[str, object]:
        return {"id": index, "start": index * 1.0, "end": index * 1.0 + 0.9, "text": text}

    def test_identical_segment_run_bounded_to_three(self) -> None:
        segments = [self._segment(index, "请不吝点赞订阅关注") for index in range(8)]
        cleaned = transcribe._collapse_repeated_segments(segments)
        self.assertEqual(len(cleaned), 3)
        self.assertEqual([segment["id"] for segment in cleaned], [0, 1, 2])

    def test_distinct_segments_all_kept(self) -> None:
        segments = [self._segment(index, f"第{index}句") for index in range(5)]
        self.assertEqual(len(transcribe._collapse_repeated_segments(segments)), 5)

    def test_punctuation_only_difference_counts_as_repeat(self) -> None:
        segments = [
            self._segment(0, "谢谢观看。"),
            self._segment(1, "谢谢观看"),
            self._segment(2, "谢谢观看！"),
            self._segment(3, "谢谢观看??"),
            self._segment(4, "下期再见"),
        ]
        cleaned = transcribe._collapse_repeated_segments(segments)
        self.assertEqual([segment["text"] for segment in cleaned][-1], "下期再见")
        self.assertEqual(len(cleaned), 4)


class UnreliableSegmentTests(unittest.TestCase):
    def test_silence_plus_low_confidence_is_unreliable(self) -> None:
        self.assertTrue(
            transcribe._segment_is_unreliable(-1.4, 0.72, _settings())
        )

    def test_confident_speech_is_kept(self) -> None:
        self.assertFalse(transcribe._segment_is_unreliable(-0.3, 0.1, _settings()))

    def test_low_confidence_over_real_speech_is_kept(self) -> None:
        # avg_logprob is low but the decoder did not flag silence.
        self.assertFalse(transcribe._segment_is_unreliable(-1.4, 0.1, _settings()))

    def test_missing_diagnostics_are_kept(self) -> None:
        self.assertFalse(transcribe._segment_is_unreliable(None, 0.9, _settings()))
        self.assertFalse(transcribe._segment_is_unreliable(-2.0, None, _settings()))

    def test_non_numeric_diagnostics_are_kept(self) -> None:
        self.assertFalse(transcribe._segment_is_unreliable("n/a", 0.9, _settings()))


class SanitizerRegressionTests(unittest.TestCase):
    def test_spaced_single_character_loop_still_bounded(self) -> None:
        cleaned = transcribe._sanitize_asr_text("哈 " * 10)
        self.assertEqual(cleaned, "哈 " * 4)

    def test_phrase_scrub_runs_after_character_bound(self) -> None:
        cleaned = transcribe._postprocess_text("哈哈 哈哈 哈哈 哈哈 哈哈 哈哈", _settings())
        self.assertEqual(cleaned, "哈哈 哈哈 哈哈")


if __name__ == "__main__":
    unittest.main()
