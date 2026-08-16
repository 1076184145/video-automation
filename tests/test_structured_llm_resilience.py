"""Tests for structured-LLM schema validation, repair retries and fallback."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from video_automation import llm_output, llm_tools
from video_automation.config import Settings
from video_automation.llm_output import (
    StructuredOutputError,
    parse_structured_json,
    validate_required_shape,
)
from video_automation.provider_errors import ProviderRequestError


class _JsonResponse:
    def __init__(self, text: str) -> None:
        self._text = text

    def __enter__(self) -> "_JsonResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self._text.encode("utf-8")


def _openai_text_response(text: str) -> dict:
    return {"output": [{"content": [{"type": "output_text", "text": text}]}]}


def _settings(**overrides: object) -> Settings:
    values = {
        "llm_provider": "openai",
        "llm_model": "gpt-test",
        "llm_fallback_provider": "",
        "llm_max_repair_retries": 2,
        "openai_api_key": "test-key",
    }
    values.update(overrides)
    return replace(Settings.load(), **values)


_METADATA_SCHEMA = {
    "type": "object",
    "required": ["titles"],
    "properties": {"titles": {"type": "array", "items": {"type": "string"}, "maxItems": 3}},
}


class ParseStructuredJsonTests(unittest.TestCase):
    def test_plain_object(self) -> None:
        self.assertEqual(parse_structured_json('{"a": 1}'), {"a": 1})

    def test_code_fence_is_stripped(self) -> None:
        self.assertEqual(parse_structured_json("```json\n{\"a\": 1}\n```"), {"a": 1})

    def test_prose_wrapped_object_is_extracted(self) -> None:
        self.assertEqual(parse_structured_json('Here you go:\n{"a": 1}'), {"a": 1})

    def test_invalid_raises_structured_output_error(self) -> None:
        with self.assertRaises(StructuredOutputError):
            parse_structured_json("no json here")


class ValidateRequiredShapeTests(unittest.TestCase):
    def test_valid_payload_passes(self) -> None:
        validate_required_shape({"titles": ["a"]}, _METADATA_SCHEMA)

    def test_missing_required_key_fails(self) -> None:
        with self.assertRaises(StructuredOutputError):
            validate_required_shape({}, _METADATA_SCHEMA)

    def test_wrong_item_type_fails(self) -> None:
        with self.assertRaises(StructuredOutputError):
            validate_required_shape({"titles": [1, 2]}, _METADATA_SCHEMA)

    def test_max_items_enforced(self) -> None:
        with self.assertRaises(StructuredOutputError):
            validate_required_shape({"titles": ["a", "b", "c", "d"]}, _METADATA_SCHEMA)

    def test_number_bounds_enforced(self) -> None:
        schema = {"type": "number", "minimum": 0, "maximum": 100}
        validate_required_shape(50, schema)
        with self.assertRaises(StructuredOutputError):
            validate_required_shape(101, schema)


class RepairRetryTests(unittest.TestCase):
    def test_schema_invalid_output_is_repaired_on_retry(self) -> None:
        settings = _settings()
        responses = [
            _openai_text_response(json.dumps({"wrong": True})),
            _openai_text_response(json.dumps({"titles": ["fixed"]})),
        ]
        with patch.object(
            llm_tools.urllib.request, "urlopen", side_effect=[_JsonResponse(json.dumps(r)) for r in responses]
        ) as urlopen:
            result = llm_tools.call_structured_llm(
                settings,
                system="Return metadata.",
                user="clip",
                schema=_METADATA_SCHEMA,
                schema_name="video_metadata",
            )
        self.assertEqual(result, {"titles": ["fixed"]})
        self.assertEqual(urlopen.call_count, 2)
        # The repair prompt carries the validation error back to the model.
        second_body = json.loads(urlopen.call_args_list[1].args[0].data.decode("utf-8"))
        self.assertIn("rejected", second_body["input"][1]["content"])

    def test_exhausted_repairs_raise_provider_error(self) -> None:
        settings = _settings(llm_max_repair_retries=1)
        bad = _JsonResponse(json.dumps(_openai_text_response(json.dumps({"wrong": True}))))
        with patch.object(llm_tools.urllib.request, "urlopen", side_effect=[bad, bad]):
            with self.assertRaises(ProviderRequestError) as ctx:
                llm_tools.call_structured_llm(
                    settings,
                    system="s",
                    user="u",
                    schema=_METADATA_SCHEMA,
                    schema_name="video_metadata",
                )
        self.assertEqual(ctx.exception.code, "response_invalid")

    def test_markdown_fenced_json_is_recovered_without_retry(self) -> None:
        settings = _settings()
        fenced = "```json\n{\"titles\": [\"ok\"]}\n```"
        response = _JsonResponse(json.dumps(_openai_text_response(fenced)))
        with patch.object(llm_tools.urllib.request, "urlopen", return_value=response) as urlopen:
            result = llm_tools.call_structured_llm(
                settings, system="s", user="u", schema=_METADATA_SCHEMA, schema_name="video_metadata"
            )
        self.assertEqual(result, {"titles": ["ok"]})
        self.assertEqual(urlopen.call_count, 1)


class ProviderFallbackTests(unittest.TestCase):
    def test_provider_chain_orders_primary_then_fallback(self) -> None:
        self.assertEqual(
            llm_tools._structured_llm_provider_chain(_settings(llm_fallback_provider="local")),
            ["openai", "local"],
        )
        self.assertEqual(llm_tools._structured_llm_provider_chain(_settings()), ["openai"])
        # Duplicate fallback is ignored.
        self.assertEqual(
            llm_tools._structured_llm_provider_chain(_settings(llm_fallback_provider="openai")),
            ["openai"],
        )

    def test_primary_network_error_falls_back_to_local(self) -> None:
        settings = _settings(llm_fallback_provider="local")
        local_payload = {"titles": ["local-answer"]}
        with patch.object(
            llm_tools.urllib.request, "urlopen", side_effect=OSError("connection reset")
        ), patch.object(
            llm_tools,
            "_structured_attempt",
            side_effect=[OSError("connection reset"), local_payload],
        ) as attempt:
            result = llm_tools.call_structured_llm(
                settings, system="s", user="u", schema=_METADATA_SCHEMA, schema_name="video_metadata"
            )
        self.assertEqual(result, local_payload)
        self.assertEqual([call.args[1] for call in attempt.call_args_list], ["openai", "local"])

    def test_no_fallback_preserves_original_error(self) -> None:
        settings = _settings()
        with patch.object(
            llm_tools,
            "_structured_attempt",
            side_effect=ProviderRequestError("OpenAI", "structured request", "credentials_missing", "no key"),
        ) as attempt:
            with self.assertRaises(ProviderRequestError) as ctx:
                llm_tools.call_structured_llm(
                    settings, system="s", user="u", schema=_METADATA_SCHEMA, schema_name="video_metadata"
                )
        self.assertEqual(ctx.exception.code, "credentials_missing")
        self.assertEqual(attempt.call_count, 1)


class LocalAiDelegationTests(unittest.TestCase):
    def test_local_parse_and_validate_wrap_shared_errors(self) -> None:
        from video_automation import local_ai

        with self.assertRaises(ProviderRequestError) as ctx:
            local_ai._parse_json_object("not json")
        self.assertEqual(ctx.exception.code, "response_invalid")

        with self.assertRaises(ProviderRequestError) as ctx:
            local_ai._validate_required_shape({"titles": 5}, _METADATA_SCHEMA)
        self.assertEqual(ctx.exception.code, "response_invalid")

        self.assertEqual(local_ai._parse_json_object('{"ok": 1}'), {"ok": 1})


class SharedModuleSmokeTests(unittest.TestCase):
    def test_llm_output_module_exposes_helpers(self) -> None:
        self.assertTrue(callable(llm_output.parse_structured_json))
        self.assertTrue(callable(llm_output.validate_required_shape))
        self.assertIs(StructuredOutputError, llm_output.StructuredOutputError)


if __name__ == "__main__":
    unittest.main()
