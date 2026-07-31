from __future__ import annotations

import json
from typing import Any


PROVIDER_ERROR_CODES = {
    "credentials_missing",
    "credentials_invalid",
    "model_missing",
    "model_unavailable",
    "provider_unsupported",
    "quota_exhausted",
    "rate_limited",
    "network_error",
    "response_invalid",
    "provider_error",
}


class ProviderRequestError(RuntimeError):
    def __init__(
        self,
        provider: str,
        operation: str,
        code: str,
        message: str,
        *,
        http_status: int | None = None,
    ) -> None:
        normalized_code = code if code in PROVIDER_ERROR_CODES else "provider_error"
        self.provider = str(provider or "AI provider").strip() or "AI provider"
        self.operation = str(operation or "request").strip() or "request"
        self.code = normalized_code
        self.http_status = http_status
        detail = _safe_provider_message(message)
        status_text = f" (HTTP {http_status})" if http_status is not None else ""
        super().__init__(
            f"{self.provider} {self.operation} failed [{normalized_code}]{status_text}: {detail}"
        )


def provider_configuration_error(
    provider: str,
    operation: str,
    code: str,
    message: str,
) -> ProviderRequestError:
    return ProviderRequestError(provider, operation, code, message)


def provider_http_error(
    provider: str,
    operation: str,
    http_status: int,
    response_body: str,
) -> ProviderRequestError:
    message, provider_code = _provider_error_detail(response_body)
    code = _classify_provider_error(http_status, provider_code, message)
    return ProviderRequestError(
        provider,
        operation,
        code,
        message,
        http_status=http_status,
    )


def provider_network_error(
    provider: str,
    operation: str,
    error: BaseException,
) -> ProviderRequestError:
    return ProviderRequestError(
        provider,
        operation,
        "network_error",
        str(error) or type(error).__name__,
    )


def provider_error_code(error: BaseException) -> str:
    if isinstance(error, ProviderRequestError):
        return error.code
    return "provider_error"


def _provider_error_detail(response_body: str) -> tuple[str, str]:
    body = str(response_body or "").strip()
    if not body:
        return "The provider returned an empty error response.", ""
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return _safe_provider_message(body), ""
    if not isinstance(payload, dict):
        return _safe_provider_message(body), ""
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("detail") or error.get("error")
        code = error.get("code") or error.get("type")
        return _safe_provider_message(message or body), str(code or "").strip()
    if isinstance(error, str):
        return _safe_provider_message(error), str(payload.get("code") or "").strip()
    message = payload.get("message") or payload.get("detail") or body
    code = payload.get("code") or payload.get("type")
    return _safe_provider_message(message), str(code or "").strip()


def _classify_provider_error(http_status: int, provider_code: str, message: str) -> str:
    combined = f"{provider_code} {message}".lower()
    if http_status in {401, 403} or any(
        marker in combined
        for marker in (
            "invalid api key",
            "invalid_api_key",
            "authentication",
            "unauthorized",
            "user not found",
        )
    ):
        return "credentials_invalid"
    if http_status == 429 and any(
        marker in combined
        for marker in (
            "insufficient_quota",
            "quota",
            "billing",
            "credit",
        )
    ):
        return "quota_exhausted"
    if http_status == 429:
        return "rate_limited"
    if http_status == 404 or any(
        marker in combined
        for marker in (
            "model not found",
            "model_not_found",
            "model does not exist",
            "unsupported model",
        )
    ):
        return "model_unavailable"
    return "provider_error"


def _safe_provider_message(value: Any, *, max_chars: int = 1000) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return (text or "The provider request failed.")[:max_chars]
