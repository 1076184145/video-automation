from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from video_automation.library_api import dispatch_library_request


class AutomationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = SimpleNamespace(root=root, jobs_dir=root / "processing" / "jobs")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_recipe_routes_and_capability_use_versioned_contract(self) -> None:
        status, capabilities = dispatch_library_request(
            self.settings, "GET", "/api/v1/capabilities"
        )
        self.assertEqual(status, 200)
        self.assertTrue(capabilities["features"]["recipes"])

        status, created = dispatch_library_request(
            self.settings,
            "POST",
            "/api/v1/recipes",
            {
                "name": "一键横屏",
                "stages": ["transcribe", "render_final"],
                "options": {"vertical": False},
                "target_platforms": ["bilibili"],
            },
        )
        self.assertEqual(status, 201)

        status, listed = dispatch_library_request(self.settings, "GET", "/api/v1/recipes")
        self.assertEqual(status, 200)
        self.assertEqual(listed["items"][0]["id"], created["id"])

        status, updated = dispatch_library_request(
            self.settings,
            "POST",
            f"/api/v1/recipes/{created['id']}",
            {"name": "一键横屏 2"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["name"], "一键横屏 2")

    def test_recipe_import_reports_created_and_existing_items(self) -> None:
        payload = {
            "items": [
                {
                    "client_id": "browser-profile-1",
                    "name": "浏览器旧预设",
                    "stages": ["transcribe"],
                    "options": {"skip_transcribe": False},
                    "target_platforms": [],
                }
            ]
        }
        first_status, first = dispatch_library_request(
            self.settings, "POST", "/api/v1/recipes/import", payload
        )
        second_status, second = dispatch_library_request(
            self.settings, "POST", "/api/v1/recipes/import", payload
        )

        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 200)
        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["existing"], 1)


if __name__ == "__main__":
    unittest.main()
