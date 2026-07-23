from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from video_automation.config import Settings
from video_automation import health


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


if __name__ == "__main__":
    unittest.main()
