from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video_automation.automation import AutomationRepository


class AutomationRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = AutomationRepository(Path(self.temp_dir.name) / "library.sqlite3")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_recipe_crud_preserves_structured_processing_options(self) -> None:
        recipe = self.repository.create_recipe(
            {
                "name": "B站横屏自动处理",
                "stages": ["transcribe", "detect_silence", "render_final"],
                "options": {"detect_silence": True, "vertical": False},
                "creator_kit_id": "kit-bilibili",
                "target_platforms": ["bilibili"],
            }
        )

        self.assertTrue(recipe["id"].startswith("recipe_"))
        self.assertEqual(recipe["stages"][0], "transcribe")
        self.assertTrue(recipe["options"]["detect_silence"])
        self.assertEqual(self.repository.list_recipes()[0]["target_platforms"], ["bilibili"])

        updated = self.repository.update_recipe(
            recipe["id"],
            {"name": "B站横屏极速", "options": {"detect_silence": False}},
        )
        self.assertEqual(updated["name"], "B站横屏极速")
        self.assertFalse(updated["options"]["detect_silence"])
        self.assertEqual(updated["stages"], recipe["stages"])

        self.assertTrue(self.repository.delete_recipe(recipe["id"]))
        self.assertIsNone(self.repository.get_recipe(recipe["id"]))

    def test_client_import_is_idempotent_and_keeps_one_server_recipe(self) -> None:
        values = {
            "name": "旧版自定义预设",
            "stages": ["transcribe", "render_final"],
            "options": {"vertical": True},
            "target_platforms": ["douyin"],
        }

        first, first_created = self.repository.import_client_recipe("local-profile-1", values)
        second, second_created = self.repository.import_client_recipe("local-profile-1", values)

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.repository.list_recipes()), 1)

    def test_recipe_rejects_invalid_structured_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "stages"):
            self.repository.create_recipe({"name": "Bad", "stages": "transcribe"})
        with self.assertRaisesRegex(ValueError, "options"):
            self.repository.create_recipe({"name": "Bad", "options": []})


if __name__ == "__main__":
    unittest.main()
