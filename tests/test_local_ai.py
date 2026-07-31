from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from video_automation import covers, local_ai
from video_automation.config import Settings
from video_automation.io_utils import write_json_atomic
from video_automation.provider_errors import ProviderRequestError


class _JsonResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.status = 200

    def __enter__(self) -> "_JsonResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class LocalAiTests(unittest.TestCase):
    def test_cover_dimensions_follow_requested_platform_aspect(self) -> None:
        self.assertEqual(local_ai._local_cover_dimensions("9:16", 1024), (576, 1024))
        self.assertEqual(local_ai._local_cover_dimensions("16:9", 1024), (1024, 576))

    def test_local_cover_reference_is_cropped_to_target_aspect(self) -> None:
        from PIL import Image, ImageDraw

        source = Image.new("RGB", (1280, 720), "red")
        ImageDraw.Draw(source).rectangle((300, 72, 980, 648), fill="green")

        fitted = local_ai._fit_local_cover_reference(source, 576, 1024)

        self.assertEqual(fitted.size, (576, 1024))
        self.assertEqual(fitted.getpixel((288, 512)), (0, 128, 0))
        self.assertEqual(fitted.getpixel((0, 512)), (0, 128, 0))

    def test_json_parser_accepts_fenced_model_output(self) -> None:
        parsed = local_ai._parse_json_object('```json\n{"summary":"可用","highlights":[]}\n```')
        self.assertEqual(parsed["summary"], "可用")

    def test_local_llm_endpoint_rejects_non_loopback_hosts(self) -> None:
        with self.assertRaisesRegex(ProviderRequestError, "loopback"):
            local_ai._loopback_host_port("http://192.168.1.8:8766/v1")

    def test_cover_health_requires_weight_shards_not_only_model_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir)
            (model_path / "model_index.json").write_text("{}", encoding="utf-8")
            check = local_ai._cover_model_check(model_path, required=True)
            self.assertFalse(check["exists"])
            self.assertEqual(check["status"], "missing")
            self.assertIn("model weights", check["version"])

    def test_local_structured_llm_uses_schema_constrained_chat_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            model_path = Path(temp_dir) / "model.gguf"
            model_path.write_bytes(b"gguf")
            settings = replace(
                Settings.load(),
                llm_provider="local",
                llm_model="local-text-model",
                local_llm_model_path=model_path,
                local_llm_base_url="http://127.0.0.1:8766/v1",
            )
            schema = {
                "type": "object",
                "required": ["summary", "highlights"],
                "properties": {
                    "summary": {"type": "string"},
                    "highlights": {"type": "array", "items": {"type": "object"}},
                },
            }
            response = {
                "choices": [
                    {
                        "message": {
                            "content": '{"summary":"韩语内容已理解","highlights":[]}',
                        },
                    },
                ],
            }

            with (
                patch.object(local_ai, "ensure_local_llm_server"),
                patch.object(
                    local_ai.urllib.request,
                    "urlopen",
                    return_value=_JsonResponse(response),
                ) as urlopen,
            ):
                result = local_ai.call_local_structured_llm(
                    settings,
                    system="system",
                    user="한국어 transcript",
                    schema=schema,
                    schema_name="semantic_highlights",
                )

            self.assertEqual(result["summary"], "韩语内容已理解")
            request = urlopen.call_args.args[0]
            self.assertEqual(
                request.full_url,
                "http://127.0.0.1:8766/v1/chat/completions",
            )
            body = json.loads(request.data.decode("utf-8"))
            self.assertEqual(body["response_format"]["type"], "json_object")
            self.assertEqual(body["response_format"]["schema"], schema)
            self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
            self.assertEqual(body["reasoning_effort"], "none")
            self.assertIn("/no_think", body["messages"][1]["content"])

    def test_local_cover_provider_needs_no_api_key_and_receives_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            reference_path = job_dir / "reference.jpg"
            reference_path.write_bytes(b"reference")
            write_json_atomic(job_dir / "manifest.json", {"source_name": "source.mp4"})
            settings = replace(
                Settings.load(),
                cover_provider="local",
                cover_model="local-image-model",
                cover_api_key="",
                openai_api_key="",
            )
            generated = {
                "data": [
                    {"b64_json": "AA==", "revised_prompt": "local"}
                    for _ in range(3)
                ],
            }

            with (
                patch.object(covers, "_prepare_cover_reference", return_value=reference_path),
                patch.object(covers, "_postprocess_cover"),
                patch.object(
                    local_ai,
                    "generate_local_cover_images",
                    return_value=generated,
                ) as generate,
                patch.object(local_ai, "release_local_ai") as release,
            ):
                manifest = covers.generate_cover_candidates(
                    settings,
                    job_dir,
                    count=3,
                    aspects=["9:16"],
                )

            self.assertEqual(manifest["status"], "ready")
            self.assertEqual(len(manifest["candidates"]["9:16"]), 3)
            self.assertEqual(
                generate.call_args.kwargs["reference_path"],
                reference_path,
            )
            self.assertEqual(generate.call_args.kwargs["aspect"], "9:16")
            release.assert_called_once_with()

    def test_resolve_server_accepts_configured_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "llama-server.exe"
            executable.write_bytes(b"exe")
            settings = replace(
                Settings.load(),
                local_llm_server_path=executable,
            )
            self.assertEqual(
                local_ai.resolve_local_llm_server(settings),
                executable.resolve(),
            )

    def test_server_command_uses_one_gpu_and_disables_unused_browser_surface(self) -> None:
        settings = replace(
            Settings.load(),
            local_llm_model_path=Path("model.gguf"),
        )
        command = local_ai._local_llm_server_command(
            settings,
            Path("llama-server"),
            "127.0.0.1",
            8766,
        )
        self.assertEqual(
            command[command.index("--alias") + 1],
            settings.llm_model,
        )
        self.assertEqual(command[command.index("--split-mode") + 1], "none")
        self.assertEqual(command[command.index("--main-gpu") + 1], "0")
        self.assertEqual(command[command.index("--reasoning") + 1], "off")
        self.assertIn("--offline", command)
        self.assertEqual(command[command.index("--cors-origins") + 1], "localhost")
        self.assertIn("--no-webui", command)
        self.assertIn("--no-slots", command)


if __name__ == "__main__":
    unittest.main()
