from __future__ import annotations

import http.client
import importlib.util
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from video_automation.api import create_server
from video_automation.config import Settings
from video_automation import health
from video_automation.io_utils import write_json_atomic
from video_automation.jobs import Job


class HealthCacheTests(unittest.TestCase):
    def tearDown(self) -> None:
        health.clear_health_cache()

    def test_cache_is_reused_only_for_equivalent_settings(self) -> None:
        base = Settings.load()
        changed = replace(base, api_host="localhost" if base.api_host != "localhost" else "127.0.0.2")

        with patch.object(health, "_build_health_payload", side_effect=lambda settings: {"host": settings.api_host}) as build:
            first = health.health_payload(base)
            repeated = health.health_payload(base)
            second = health.health_payload(changed)

        self.assertEqual(first, repeated)
        self.assertIsNot(first, repeated)
        self.assertEqual(second["host"], changed.api_host)
        self.assertEqual(build.call_count, 2)

    def test_caller_mutation_does_not_pollute_cached_health_payload(self) -> None:
        settings = Settings.load()

        with patch.object(health, "_build_health_payload", return_value={"ok": True, "warnings": []}) as build:
            response = health.health_payload(settings)
            response["changed"] = ["WHISPER_MODEL"]
            response["warnings"].append({"code": "caller_only"})
            repeated = health.health_payload(settings)

        self.assertNotIn("changed", repeated)
        self.assertEqual(repeated["warnings"], [])
        self.assertEqual(build.call_count, 1)

    def test_cache_key_tracks_secret_presence_without_containing_secret_text(self) -> None:
        settings = replace(Settings.load(), openai_api_key="do-not-cache-this-secret")

        key = health._health_settings_cache_key(settings)

        self.assertNotIn("do-not-cache-this-secret", repr(key))
        self.assertIn(("openai_api_key", True), key)

    def test_blocked_remote_binding_fails_health_but_explicit_opt_in_does_not(self) -> None:
        base = Settings.load()
        blocked = replace(base, api_host="0.0.0.0", api_allow_remote=False)
        opted_in = replace(base, api_host="0.0.0.0", api_allow_remote=True)
        with (
            patch.object(health, "_path_exists", return_value=True),
            patch.object(health, "_first_version_line", return_value=""),
            patch.object(health, "_transcription_runtime_checks", return_value=[]),
            patch.object(health, "_render_runtime_checks", return_value=[]),
            patch.object(health, "_cover_runtime_checks", return_value=[]),
            patch.object(health, "_optional_module_checks", return_value=[]),
            patch.object(health, "_storage_health", return_value={"available": True, "low_space": False}),
            patch.object(health, "legacy_secret_keys", return_value=set()),
        ):
            blocked_payload = health._build_health_payload(blocked)
            opted_in_payload = health._build_health_payload(opted_in)

        self.assertFalse(blocked_payload["ok"])
        self.assertEqual(blocked_payload["warnings"][0]["code"], "remote_api_blocked")
        self.assertTrue(opted_in_payload["ok"])
        self.assertEqual(opted_in_payload["warnings"][0]["code"], "remote_api_exposed")


class FrameCoverFallbackAgreementTests(unittest.TestCase):
    """GET /health reports frame-cover availability that the cover route then enforces."""

    MISSING_KEY = "COVER_API_KEY, OPENAI_API_KEY, or GOOGLE_API_KEY is not configured"

    def setUp(self) -> None:
        health.clear_health_cache()

    def tearDown(self) -> None:
        health.clear_health_cache()

    def _health_entry_and_frame_request(self, *, fallback: bool, model: bool, cover_key: str = ""):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model_dir = root / "model"
            if model:
                model_dir.mkdir()
                (model_dir / "model_index.json").write_text("{}", encoding="utf-8")
                (model_dir / "weights.safetensors").write_bytes(b"stand-in")
            job_dir = root / "jobs" / "cover-job"
            job_dir.mkdir(parents=True)
            source = root / "source.mp4"
            source.write_bytes(b"")
            Job(source, job_dir, status="needs_review").save()
            write_json_atomic(job_dir / "manifest.json", {"source_path": str(source), "source_name": "source"})
            settings = replace(
                Settings.load(), root=root, jobs_dir=root / "jobs", logs_dir=root / "logs",
                input_recordings_dir=root / "recordings", api_host="127.0.0.1", api_port=0,
                cover_provider="openai", cover_api_key=cover_key, openai_api_key="", google_api_key="",
                cover_fallback_local=fallback, local_cover_model_path=model_dir, llm_provider="openai",
                publish_enabled=False,
            )
            with (
                patch.object(Settings, "load", return_value=settings),
                patch.object(health, "_first_version_line", return_value=""),
                patch.object(health, "_transcription_runtime_checks", return_value=[]),
                patch.object(health, "_render_runtime_checks", return_value=[]),
                patch.object(health, "_optional_module_checks", return_value=[]),
                patch.object(health, "_storage_health", return_value={"available": False}),
            ):
                server = create_server(settings, start_queue_worker=False)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                connection = http.client.HTTPConnection(*server.server_address, timeout=10)
                try:
                    connection.request("GET", "/health")
                    checks = json.loads(connection.getresponse().read().decode("utf-8"))["checks"]
                    body = json.dumps({"title": "Title", "frame_only": True}).encode("utf-8")
                    connection.request(
                        "POST", "/jobs/cover-job/covers/generate", body=body,
                        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
                    )
                    reply = connection.getresponse()
                    answer = (reply.status, json.loads(reply.read().decode("utf-8")))
                finally:
                    connection.close()
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)
        entry = next((check for check in checks if check["name"] == "cover_frame_fallback"), None)
        return entry, answer

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow is optional")
    def test_fallback_on_without_a_local_model_is_reported_available_and_queued(self) -> None:
        entry, (status, body) = self._health_entry_and_frame_request(fallback=True, model=False)

        self.assertEqual(
            {key: entry[key] for key in ("exists", "required", "optional", "status")},
            {"exists": True, "required": False, "optional": True, "status": "ok"},
        )
        self.assertEqual(status, 202)
        self.assertEqual(sorted(body), ["cover", "job", "queue"])

    def test_fallback_off_is_reported_unavailable_and_refused(self) -> None:
        entry, (status, body) = self._health_entry_and_frame_request(fallback=False, model=False)

        self.assertEqual((entry["exists"], entry["status"]), (False, "optional_missing"))
        self.assertEqual((status, body), (400, {"error": self.MISSING_KEY}))

    def test_local_model_with_a_remote_provider_is_reported_unavailable_and_refused(self) -> None:
        entry, (status, body) = self._health_entry_and_frame_request(fallback=True, model=True)

        self.assertEqual((entry["exists"], entry["status"]), (False, "optional_missing"))
        self.assertEqual((status, body), (400, {"error": self.MISSING_KEY}))

    def test_configured_cover_key_reports_no_frame_fallback_and_refuses_frame_only(self) -> None:
        entry, (status, body) = self._health_entry_and_frame_request(fallback=True, model=False, cover_key="cover-key")

        self.assertIsNone(entry)
        self.assertEqual(
            (status, body), (400, {"error": "Frame fallback is unavailable with the current cover settings"}),
        )


if __name__ == "__main__":
    unittest.main()
