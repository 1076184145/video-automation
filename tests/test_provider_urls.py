from __future__ import annotations

import unittest

from video_automation import covers, llm_tools
from video_automation.url_security import require_http_url


class ProviderUrlTests(unittest.TestCase):
    def test_provider_url_builders_reject_non_http_schemes(self) -> None:
        for build in (
            lambda: covers._join_url("file:///tmp/provider", "images"),
            lambda: covers._google_model_url("file:///tmp/provider", "model"),
            lambda: llm_tools._google_model_url("file:///tmp/provider", "model"),
        ):
            with self.subTest(build=build):
                with self.assertRaisesRegex(ValueError, "http or https"):
                    build()

    def test_http_urls_and_custom_paths_remain_valid(self) -> None:
        self.assertEqual(require_http_url("https://api.example.test/v1"), "https://api.example.test/v1")
        self.assertEqual(covers._join_url("http://localhost:8080/v1", "images"),
                         "http://localhost:8080/v1/images")


if __name__ == "__main__":
    unittest.main()
