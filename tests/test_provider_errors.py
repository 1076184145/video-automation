from __future__ import annotations

import unittest

from video_automation.provider_errors import (
    ProviderRequestError,
    provider_http_error,
    provider_network_error,
)


class ProviderErrorTests(unittest.TestCase):
    def test_openai_insufficient_quota_is_distinct_from_rate_limit(self) -> None:
        error = provider_http_error(
            "OpenAI",
            "structured request",
            429,
            '{"error":{"message":"You exceeded your current quota.","code":"insufficient_quota"}}',
        )

        self.assertIsInstance(error, ProviderRequestError)
        self.assertEqual(error.code, "quota_exhausted")
        self.assertEqual(error.http_status, 429)
        self.assertIn("[quota_exhausted]", str(error))
        self.assertNotIn("insufficient_quota", str(error))

    def test_openrouter_user_not_found_is_invalid_credentials(self) -> None:
        error = provider_http_error(
            "OpenRouter",
            "image generation",
            401,
            '{"error":{"message":"User not found."}}',
        )

        self.assertEqual(error.code, "credentials_invalid")
        self.assertIn("User not found.", str(error))

    def test_generic_429_is_retryable_rate_limit(self) -> None:
        error = provider_http_error(
            "OpenAI",
            "structured request",
            429,
            '{"error":{"message":"Too many requests.","code":"rate_limit_exceeded"}}',
        )

        self.assertEqual(error.code, "rate_limited")

    def test_network_errors_have_stable_code(self) -> None:
        error = provider_network_error(
            "OpenRouter",
            "image generation",
            OSError("temporary DNS failure"),
        )

        self.assertEqual(error.code, "network_error")
        self.assertIn("temporary DNS failure", str(error))


if __name__ == "__main__":
    unittest.main()
