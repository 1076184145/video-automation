from __future__ import annotations

import ipaddress
from typing import Any


class UnsafeAPIBindingError(RuntimeError):
    """Raised when the API would be exposed without an explicit opt-in."""


def is_loopback_api_host(host: str) -> bool:
    value = str(host or "").strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    normalized = value.rstrip(".").lower()
    if normalized == "localhost":
        return True
    if not normalized:
        return False
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def api_binding_status(host: str, allow_remote: bool) -> dict[str, Any]:
    remote_binding = not is_loopback_api_host(host)
    allowed = not remote_binding or bool(allow_remote)
    warning_code = ""
    message = ""
    if remote_binding and allowed:
        warning_code = "remote_api_exposed"
        message = (
            "The API is listening beyond loopback. Protect it with a firewall and "
            "authenticated reverse proxy; API_ALLOW_REMOTE is not authentication."
        )
    elif remote_binding:
        warning_code = "remote_api_blocked"
        message = (
            "Non-loopback API binding is blocked. Keep API_HOST=127.0.0.1 or set "
            "API_ALLOW_REMOTE=true only after adding network access controls."
        )
    return {
        "host": str(host or ""),
        "remote_binding": remote_binding,
        "allow_remote": bool(allow_remote),
        "allowed": allowed,
        "warning_code": warning_code,
        "message": message,
    }


def require_safe_api_binding(settings: Any) -> dict[str, Any]:
    status = api_binding_status(
        str(getattr(settings, "api_host", "")),
        bool(getattr(settings, "api_allow_remote", False)),
    )
    if not status["allowed"]:
        raise UnsafeAPIBindingError(str(status["message"]))
    return status
