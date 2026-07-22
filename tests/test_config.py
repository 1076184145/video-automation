from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_automation import config
from video_automation.credentials import MemoryCredentialStore


class EnvConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_root = config.PROJECT_ROOT
        self.original_cache = dict(config._ENV_FILE_CACHE)
        self.original_env: dict[str, str | None] = {}

    def tearDown(self) -> None:
        config.PROJECT_ROOT = self.original_root
        config._ENV_FILE_CACHE.clear()
        config._ENV_FILE_CACHE.update(self.original_cache)
        for key, value in self.original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _remember_env(self, *keys: str) -> None:
        for key in keys:
            self.original_env[key] = os.environ.get(key)
            os.environ.pop(key, None)

    def test_env_file_precedence_and_empty_environment_override(self) -> None:
        self._remember_env("VIDEO_AUTOMATION_TEST_KEY")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env.example").write_text("VIDEO_AUTOMATION_TEST_KEY=example\n", encoding="utf-8")
            (root / ".env").write_text("VIDEO_AUTOMATION_TEST_KEY=file\n", encoding="utf-8")
            config.PROJECT_ROOT = root
            config._ENV_FILE_CACHE.clear()

            self.assertEqual(config._env("VIDEO_AUTOMATION_TEST_KEY"), "file")

            os.environ["VIDEO_AUTOMATION_TEST_KEY"] = ""
            self.assertEqual(config._env("VIDEO_AUTOMATION_TEST_KEY", "fallback"), "")

    def test_cached_env_file_reloads_when_file_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".env"
            path.write_text("VALUE=one\n", encoding="utf-8")
            config._ENV_FILE_CACHE.clear()

            self.assertEqual(config._cached_env_file(path)["VALUE"], "one")
            path.write_text("VALUE=two\n", encoding="utf-8")
            os.utime(path, None)

            self.assertEqual(config._cached_env_file(path)["VALUE"], "two")

    def test_google_ai_studio_settings_load_from_environment(self) -> None:
        self._remember_env("GOOGLE_API_KEY", "GOOGLE_BASE_URL", "LLM_PROVIDER", "COVER_PROVIDER")
        os.environ.update({
            "GOOGLE_API_KEY": "google-test-key",
            "GOOGLE_BASE_URL": "https://example.test/v1beta",
            "LLM_PROVIDER": "google",
            "COVER_PROVIDER": "google",
        })

        settings = config.Settings.load()

        self.assertEqual(settings.google_api_key, "google-test-key")
        self.assertEqual(settings.google_base_url, "https://example.test/v1beta")
        self.assertEqual(settings.llm_provider, "google")
        self.assertEqual(settings.cover_provider, "google")

    def test_secret_reference_loads_from_os_credential_store(self) -> None:
        self._remember_env("OPENAI_API_KEY", "OPENAI_API_KEY_REF", "VIDEO_AUTOMATION_ROOT")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text(
                "OPENAI_API_KEY_REF=video-automation/config/openai-api-key\n",
                encoding="utf-8",
            )
            store = MemoryCredentialStore()
            store.set("video-automation/config/openai-api-key", "keyring-secret")
            config.PROJECT_ROOT = root
            config._ENV_FILE_CACHE.clear()

            with patch.object(config, "SystemCredentialStore", return_value=store):
                settings = config.Settings.load()

        self.assertEqual(settings.openai_api_key, "keyring-secret")

    def test_process_environment_secret_overrides_keyring_reference(self) -> None:
        self._remember_env("OPENAI_API_KEY", "OPENAI_API_KEY_REF")
        os.environ.update({
            "OPENAI_API_KEY": "process-secret",
            "OPENAI_API_KEY_REF": "video-automation/config/openai-api-key",
        })
        store = MemoryCredentialStore()
        store.set("video-automation/config/openai-api-key", "keyring-secret")

        with patch.object(config, "SystemCredentialStore", return_value=store):
            settings = config.Settings.load()

        self.assertEqual(settings.openai_api_key, "process-secret")

    def test_batch_and_upload_limits_load_from_environment(self) -> None:
        self._remember_env("API_BATCH_LIMIT", "RECORDING_UPLOAD_MAX_BYTES")
        os.environ.update({
            "API_BATCH_LIMIT": "12",
            "RECORDING_UPLOAD_MAX_BYTES": "1048576",
        })

        settings = config.Settings.load()

        self.assertEqual(settings.api_batch_limit, 12)
        self.assertEqual(settings.recording_upload_max_bytes, 1048576)

    def test_remote_api_binding_requires_explicit_configuration(self) -> None:
        self._remember_env("API_HOST", "API_ALLOW_REMOTE")
        os.environ.update({"API_HOST": "0.0.0.0", "API_ALLOW_REMOTE": "true"})

        settings = config.Settings.load()

        self.assertEqual(settings.api_host, "0.0.0.0")
        self.assertTrue(settings.api_allow_remote)

    def test_native_waveform_enabled_loads_from_environment(self) -> None:
        self._remember_env("NATIVE_WAVEFORM_ENABLED")
        os.environ["NATIVE_WAVEFORM_ENABLED"] = "false"

        settings = config.Settings.load()

        self.assertFalse(settings.native_waveform_enabled)

    def test_native_cuts_enabled_loads_from_environment(self) -> None:
        self._remember_env("NATIVE_CUTS_ENABLED")
        os.environ["NATIVE_CUTS_ENABLED"] = "false"

        settings = config.Settings.load()

        self.assertFalse(settings.native_cuts_enabled)

    def test_high_quality_audio_enabled_loads_from_environment(self) -> None:
        self._remember_env("HIGH_QUALITY_AUDIO_ENABLED")
        os.environ["HIGH_QUALITY_AUDIO_ENABLED"] = "false"

        settings = config.Settings.load()

        self.assertFalse(settings.high_quality_audio_enabled)

    def test_bilibili_connector_endpoints_load_from_env_file_settings(self) -> None:
        keys = (
            "BILIBILI_API_BASE_URL",
            "BILIBILI_VALIDATE_PATH",
            "BILIBILI_CREATE_UPLOAD_PATH",
            "BILIBILI_COMPLETE_UPLOAD_PATH",
            "BILIBILI_PUBLISH_PATH",
            "BILIBILI_QUERY_PATH",
        )
        self._remember_env(*keys)
        os.environ.update({
            "BILIBILI_API_BASE_URL": "https://sandbox.test",
            "BILIBILI_VALIDATE_PATH": "/validate",
            "BILIBILI_CREATE_UPLOAD_PATH": "/upload/init",
            "BILIBILI_COMPLETE_UPLOAD_PATH": "/upload/complete",
            "BILIBILI_PUBLISH_PATH": "/publish",
            "BILIBILI_QUERY_PATH": "/query/{remote_id}",
        })

        settings = config.Settings.load()

        self.assertEqual(settings.bilibili_api_base_url, "https://sandbox.test")
        self.assertEqual(settings.bilibili_api_endpoints["query"], "/query/{remote_id}")

    def test_x264_render_settings_load_from_environment(self) -> None:
        self._remember_env("RENDER_X264_PRESET", "RENDER_X264_CRF")
        os.environ.update({
            "RENDER_X264_PRESET": "veryfast",
            "RENDER_X264_CRF": "23",
        })

        settings = config.Settings.load()

        self.assertEqual(settings.render_x264_preset, "veryfast")
        self.assertEqual(settings.render_x264_crf, 23)


if __name__ == "__main__":
    unittest.main()
