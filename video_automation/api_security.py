from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlsplit


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


def allowed_request_host(
    host_headers: list[str],
    *,
    bound_host: str,
    bound_port: int,
    allow_remote: bool,
    allowed_origins: tuple[str, ...],
) -> bool:
    """Reject browser DNS rebinding before any API or static route is served."""
    if len(host_headers) != 1:
        return False
    raw = host_headers[0]
    if not raw or raw != raw.strip() or any(char in raw for char in "/?#@\\, \t\r\n"):
        return False
    try:
        parsed = urlsplit(f"http://{raw}")
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    if not hostname or parsed.path or parsed.query or parsed.fragment:
        return False
    if ":" in hostname and not raw.startswith("["):
        return False
    if allow_remote and not is_loopback_api_host(bound_host):
        return True

    normalized_host = hostname.rstrip(".").lower()
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    if bound_host and bound_host not in {"0.0.0.0", "::", "[::]"}:
        local_hosts.add(bound_host.strip("[]").rstrip(".").lower())
    if normalized_host in local_hosts and (port or 80) == bound_port:
        return True
    for origin in allowed_origins:
        try:
            configured = urlsplit(origin)
            configured_port = configured.port or (443 if configured.scheme == "https" else 80)
        except ValueError:
            continue
        if configured.scheme in {"http", "https"} and configured.hostname:
            requested_port = port or (443 if configured.scheme == "https" else 80)
            if (normalized_host, requested_port) == (configured.hostname.rstrip(".").lower(), configured_port):
                return True
    return False


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
