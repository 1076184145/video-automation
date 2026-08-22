"""Tests for heuristic cover/metadata fallbacks when providers are unavailable."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from video_automation import covers, llm_tools
from video_automation.config import Settings
from video_automation.io_utils import write_json_atomic
from video_automation.provider_errors import ProviderRequestError


def _settings(**overrides: object) -> Settings:
    values = {
        "cover_provider": "openai",
        "cover_model": "gpt-image-2",
        "cover_api_key": "",
        "cover_fallback_local": True,
        "llm_provider": "openai",
        "llm_model": "gpt-test",
        "openai_api_key": "",
        "metadata_fallback_heuristic": True,
    }
    values.update(overrides)
    return replace(Settings.load(), **values)


class MetadataFallbackTests(unittest.TestCase):
    def test_provider_failure_produces_heuristic_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            write_json_atomic(job_dir / "highlights.json", {
                "summary": "主播挑战高难度副本最终通关",
                "highlights": [
                    {"reason": "团灭三次后极限翻盘", "recommended_use": "抖音首发"},
                    {"reason": " Boss 战走位细节讲解", "recommended_use": "B站"},
                ],
            })
            write_json_atomic(job_dir / "cuts.json", {
                "clips": [{"final_score": 80, "text": "最后一把直接通关"}],
            })
            settings = _settings()

            with patch.object(
                llm_tools,
                "_call_structured_llm",
                side_effect=ProviderRequestError("OpenAI", "structured request", "credentials_missing", "no key"),
            ):
                payload = llm_tools.generate_metadata(settings, job_dir)

            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["backend"], "heuristic_fallback")
            self.assertEqual(payload["generator"], "heuristic_fallback")
            self.assertEqual(payload["fallback_reason"], "credentials_missing")
            for key in ("titles", "descriptions", "tags", "hashtags", "cover_titles", "platform_notes"):
                self.assertIsInstance(payload[key], list, key)
                self.assertTrue(payload[key], key)
            self.assertTrue(payload["titles"][0])
            stored = __import__("json").loads((job_dir / "metadata.json").read_text("utf-8"))
            self.assertEqual(stored["generator"], "heuristic_fallback")

    def test_fallback_disabled_reraises_provider_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            settings = _settings(metadata_fallback_heuristic=False)
            error = ProviderRequestError("OpenAI", "structured request", "network_error", "down")
            with patch.object(llm_tools, "_call_structured_llm", side_effect=error):
                with self.assertRaises(ProviderRequestError) as ctx:
                    llm_tools.generate_metadata(settings, job_dir)
            self.assertEqual(ctx.exception.code, "network_error")

    def test_heuristic_payload_without_any_artifacts_still_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            settings = _settings()
            with patch.object(
                llm_tools,
                "_call_structured_llm",
                side_effect=ProviderRequestError("OpenAI", "structured request", "quota_exhausted", "x"),
            ):
                payload = llm_tools.generate_metadata(settings, job_dir)
            for key in ("titles", "descriptions", "tags", "hashtags", "cover_titles", "platform_notes"):
                self.assertIsInstance(payload[key], list, key)
                self.assertTrue(payload[key], key)


class KeywordAndTitleHelperTests(unittest.TestCase):
    def test_compact_title_strips_punctuation_and_bounds_length(self) -> None:
        self.assertEqual(llm_tools._compact_title("  今晚，绝地翻盘！ ", limit=6), "今晚，绝地翻"[:6])
        self.assertEqual(llm_tools._compact_title("Plain title here", limit=100), "Plain title here")

    def test_frequent_keywords_ranks_repeated_tokens(self) -> None:
        text = "通关 通关 通关 打野 打野 辅助补位"
        keywords = llm_tools._frequent_keywords(text)
        self.assertEqual(keywords[0], "通关")
        self.assertIn("打野", keywords[:3])

    def test_frequent_keywords_handles_ascii_words(self) -> None:
        keywords = llm_tools._frequent_keywords("GG GG WP wp clutch")
        self.assertIn("GG", keywords)
        self.assertIn("clutch", keywords)

    def test_frequent_keywords_empty_text(self) -> None:
        self.assertEqual(llm_tools._frequent_keywords(""), [])


class CoverFallbackTests(unittest.TestCase):
    def test_unconfigured_provider_falls_back_to_frame_composite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            reference = job_dir / "highlight_thumbnail.jpg"
            reference.write_bytes(b"frame-bytes")
            settings = _settings()  # openai without key -> credentials_missing

            with (
                patch.object(covers, "_prepare_cover_reference", return_value=reference),
                patch.object(covers, "_postprocess_cover") as postprocess,
                patch.object(covers, "_darken_cover_frame", side_effect=lambda raw, f: raw),
            ):
                manifest = covers.generate_cover_candidates(settings, job_dir, count=3, aspects=["9:16", "16:9"])

            self.assertEqual(manifest["status"], "ready")
            self.assertEqual(manifest["generator"], "fallback_frame_composite")
            self.assertEqual(manifest["fallback_reason"], "credentials_missing")
            self.assertEqual(sorted(manifest["candidates"]), ["16:9", "9:16"])
            self.assertTrue(all(c["fallback_variant"] for c in manifest["candidates"]["9:16"]))
            self.assertEqual(postprocess.call_count, 4)  # 2 variants x 2 aspects
            for call in postprocess.call_args_list:
                self.assertEqual(call.kwargs["title"], manifest["title"])

    def test_fallback_disabled_reraises_configuration_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            settings = _settings(cover_fallback_local=False)
            with self.assertRaises(ProviderRequestError) as ctx:
                covers.generate_cover_candidates(settings, job_dir)
            self.assertEqual(ctx.exception.code, "credentials_missing")

    def test_fallback_without_reference_reraises_original_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            settings = _settings()
            with patch.object(covers, "_prepare_cover_reference", return_value=None):
                with self.assertRaises(ProviderRequestError) as ctx:
                    covers.generate_cover_candidates(settings, job_dir)
            self.assertEqual(ctx.exception.code, "credentials_missing")
            import json

            manifest = json.loads((job_dir / "cover_manifest.json").read_text("utf-8"))
            self.assertEqual(manifest["status"], "failed")
            self.assertIn("No usable video frame", manifest["fallback_error"])

    def test_provider_generation_failure_also_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            reference = job_dir / "highlight_thumbnail.jpg"
            reference.write_bytes(b"frame-bytes")
            settings = _settings(cover_api_key="key")

            with (
                patch.object(
                    covers,
                    "_generate_images",
                    side_effect=ProviderRequestError("OpenAI", "image generation", "rate_limited", "429"),
                ),
                patch.object(covers, "_prepare_cover_reference", return_value=reference),
                patch.object(covers, "_postprocess_cover"),
                patch.object(covers, "_darken_cover_frame", side_effect=lambda raw, f: raw),
            ):
                manifest = covers.generate_cover_candidates(settings, job_dir, count=2, aspects=["9:16"])

            self.assertEqual(manifest["status"], "ready")
            self.assertEqual(manifest["generator"], "fallback_frame_composite")
            self.assertEqual(manifest["fallback_reason"], "rate_limited")


if __name__ == "__main__":
    unittest.main()
