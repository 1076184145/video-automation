from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video_automation.api import _normalize_env_updates, _update_env_file
from video_automation.api_settings import CredentialUpdateError, apply_settings_updates, migrate_legacy_secrets
from video_automation.credentials import MemoryCredentialStore


class SettingsApiTests(unittest.TestCase):
    def test_update_env_file_preserves_comments_and_appends_missing_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / ".env"
            env_path.write_text("# existing\nWHISPER_MODEL=medium\n", encoding="utf-8")

            changed = _update_env_file(root, {
                "WHISPER_MODEL": "large-v3",
                "ASS_FONT_SIZE": "48",
            })

            self.assertEqual(changed, {"WHISPER_MODEL", "ASS_FONT_SIZE"})
            text = env_path.read_text(encoding="utf-8")
            self.assertIn("# existing", text)
            self.assertIn("WHISPER_MODEL=large-v3", text)
            self.assertIn("# Updated from Web Settings", text)
            self.assertIn("ASS_FONT_SIZE=48", text)

    def test_normalize_env_updates_rejects_uneditable_keys(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_env_updates({"PATH": "unsafe"})

    def test_normalize_env_updates_accepts_google_provider_settings(self) -> None:
        self.assertEqual(
            _normalize_env_updates({
                "GOOGLE_API_KEY": "google-key",
                "GOOGLE_BASE_URL": "https://generativelanguage.googleapis.com/v1beta",
            }),
            {
                "GOOGLE_API_KEY": "google-key",
                "GOOGLE_BASE_URL": "https://generativelanguage.googleapis.com/v1beta",
            },
        )

    def test_normalize_env_updates_accepts_batch_pressure_settings(self) -> None:
        self.assertEqual(
            _normalize_env_updates({
                "API_BATCH_LIMIT": "12",
                "RECORDING_UPLOAD_MAX_BYTES": "1048576",
            }),
            {
                "API_BATCH_LIMIT": "12",
                "RECORDING_UPLOAD_MAX_BYTES": "1048576",
            },
        )

    def test_normalize_env_updates_accepts_native_waveform_toggle(self) -> None:
        self.assertEqual(
            _normalize_env_updates({"NATIVE_WAVEFORM_ENABLED": "false"}),
            {"NATIVE_WAVEFORM_ENABLED": "false"},
        )

    def test_normalize_env_updates_accepts_native_cuts_toggle(self) -> None:
        self.assertEqual(
            _normalize_env_updates({"NATIVE_CUTS_ENABLED": "false"}),
            {"NATIVE_CUTS_ENABLED": "false"},
        )

    def test_normalize_env_updates_accepts_high_quality_audio_toggle(self) -> None:
        self.assertEqual(
            _normalize_env_updates({"HIGH_QUALITY_AUDIO_ENABLED": "false"}),
            {"HIGH_QUALITY_AUDIO_ENABLED": "false"},
        )

    def test_normalize_env_updates_accepts_x264_render_settings(self) -> None:
        self.assertEqual(
            _normalize_env_updates({
                "RENDER_X264_PRESET": "veryfast",
                "RENDER_X264_CRF": "23",
            }),
            {
                "RENDER_X264_PRESET": "veryfast",
                "RENDER_X264_CRF": "23",
            },
        )

    def test_normalize_env_updates_rejects_removed_input_modules(self) -> None:
        for key in ("DOWNLOAD_ENABLED", "LIVE_RECORDING_ENABLED", "OLIVED_RESOLVER_PATH"):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    _normalize_env_updates({key: "true"})

    def test_secret_setting_is_stored_in_keyring_reference_not_plaintext_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("OPENAI_API_KEY=legacy-secret\nWHISPER_MODEL=small\n", encoding="utf-8")
            store = MemoryCredentialStore()

            changed = apply_settings_updates(
                root,
                {"OPENAI_API_KEY": "replacement-secret", "WHISPER_MODEL": "medium"},
                credential_store=store,
            )

            text = (root / ".env").read_text(encoding="utf-8")
            self.assertNotIn("legacy-secret", text)
            self.assertNotIn("replacement-secret", text)
            self.assertIn("OPENAI_API_KEY_REF=video-automation/config/openai-api-key", text)
            self.assertIn("WHISPER_MODEL=medium", text)
            self.assertEqual(store.get("video-automation/config/openai-api-key"), "replacement-secret")
            self.assertEqual(changed, {"OPENAI_API_KEY", "WHISPER_MODEL"})

    def test_legacy_secret_migration_removes_plaintext_only_after_keyring_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text(
                "# keep\nOPENAI_API_KEY=openai-secret\nGOOGLE_API_KEY=google-secret\n",
                encoding="utf-8",
            )
            store = MemoryCredentialStore()

            migrated = migrate_legacy_secrets(root, credential_store=store)

            text = (root / ".env").read_text(encoding="utf-8")
            self.assertNotIn("openai-secret", text)
            self.assertNotIn("google-secret", text)
            self.assertIn("# keep", text)
            self.assertEqual(migrated, {"OPENAI_API_KEY", "GOOGLE_API_KEY"})
            self.assertEqual(store.get("video-automation/config/openai-api-key"), "openai-secret")
            self.assertEqual(store.get("video-automation/config/google-api-key"), "google-secret")

    def test_failed_secret_migration_preserves_plaintext_source(self) -> None:
        class FailingStore(MemoryCredentialStore):
            def set(self, reference: str, secret: str) -> None:
                raise RuntimeError("backend unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / ".env"
            env_path.write_text("OPENAI_API_KEY=keep-until-committed\n", encoding="utf-8")

            with self.assertRaises(CredentialUpdateError):
                migrate_legacy_secrets(root, credential_store=FailingStore())

            self.assertIn("OPENAI_API_KEY=keep-until-committed", env_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
