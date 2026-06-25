from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from video_automation import config


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

    def test_batch_and_upload_limits_load_from_environment(self) -> None:
        self._remember_env("API_BATCH_LIMIT", "RECORDING_UPLOAD_MAX_BYTES")
        os.environ.update({
            "API_BATCH_LIMIT": "12",
            "RECORDING_UPLOAD_MAX_BYTES": "1048576",
        })

        settings = config.Settings.load()

        self.assertEqual(settings.api_batch_limit, 12)
        self.assertEqual(settings.recording_upload_max_bytes, 1048576)

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
