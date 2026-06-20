from __future__ import annotations

import base64
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from video_automation import covers, llm_tools
from video_automation.config import Settings
from video_automation.worker import _cover_runtime_checks, _optional_module_checks


class _JsonResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "_JsonResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class GoogleProviderTests(unittest.TestCase):
    def test_google_structured_llm_uses_native_generate_content(self) -> None:
        settings = replace(
            Settings.load(),
            llm_provider="google",
            llm_model="gemini-2.5-flash",
            google_api_key="google-key",
            google_base_url="https://generativelanguage.googleapis.com/v1beta",
        )
        response = {
            "candidates": [{
                "content": {
                    "parts": [{"text": json.dumps({"titles": ["测试标题"]}, ensure_ascii=False)}],
                },
            }],
        }

        with patch.object(llm_tools.urllib.request, "urlopen", return_value=_JsonResponse(response)) as urlopen:
            result = llm_tools.call_structured_llm(
                settings,
                system="Return video metadata.",
                user="A short Chinese livestream clip.",
                schema={
                    "type": "object",
                    "required": ["titles"],
                    "properties": {"titles": {"type": "array", "items": {"type": "string"}}},
                },
                schema_name="video_metadata",
            )

        self.assertEqual(result, {"titles": ["测试标题"]})
        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
        )
        self.assertEqual(request.get_header("X-goog-api-key"), "google-key")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["systemInstruction"]["parts"][0]["text"], "Return video metadata.")
        self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(body["generationConfig"]["responseJsonSchema"]["required"], ["titles"])

    def test_google_cover_generation_extracts_inline_image_data(self) -> None:
        settings = replace(
            Settings.load(),
            cover_provider="google",
            cover_model="gemini-2.5-flash-image",
            cover_api_key="",
            google_api_key="google-key",
            google_base_url="https://generativelanguage.googleapis.com/v1beta",
        )
        encoded = base64.b64encode(b"image-bytes").decode("ascii")
        response = {
            "candidates": [{
                "content": {
                    "parts": [
                        {"text": "Generated cover"},
                        {"inlineData": {"mimeType": "image/png", "data": encoded}},
                    ],
                },
            }],
        }
        generate = getattr(covers, "_google_generate_images")

        with patch.object(covers.urllib.request, "urlopen", return_value=_JsonResponse(response)) as urlopen:
            result = generate(settings, "Create a cover", 1, "9:16")

        self.assertEqual(result["data"], [{"b64_json": encoded, "revised_prompt": "Generated cover"}])
        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent",
        )
        self.assertEqual(request.get_header("X-goog-api-key"), "google-key")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["generationConfig"]["imageConfig"]["aspectRatio"], "9:16")
        self.assertEqual(body["generationConfig"]["responseModalities"], ["IMAGE"])

    def test_google_health_checks_use_google_key_for_llm_and_covers(self) -> None:
        settings = replace(
            Settings.load(),
            llm_provider="google",
            llm_model="gemini-2.5-flash",
            cover_provider="google",
            cover_api_key="",
            google_api_key="google-key",
        )

        cover_checks = {item["name"]: item for item in _cover_runtime_checks(settings)}
        module_checks = {item["name"]: item for item in _optional_module_checks(settings)}

        self.assertTrue(cover_checks["cover_api_key"]["exists"])
        self.assertIn("GOOGLE_API_KEY", cover_checks["cover_api_key"]["path"])
        self.assertTrue(module_checks["llm_google_api_key"]["exists"])
        self.assertTrue(module_checks["llm_google_api_key"]["required"])


if __name__ == "__main__":
    unittest.main()
