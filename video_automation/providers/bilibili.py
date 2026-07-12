from __future__ import annotations

import ipaddress
import json
import socket
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..credentials import CredentialStore


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not hostname or parsed.username or parsed.password:
        raise ValueError("Bilibili URL must be an HTTP(S) URL without embedded credentials")
    return scheme, hostname, parsed.port or (443 if scheme == "https" else 80)


class BilibiliHttpTransport:
    """Configurable transport for an approved Bilibili Open Platform app or sandbox."""

    REQUIRED_ENDPOINTS = {"validate", "create_upload", "complete_upload", "publish", "query"}

    def __init__(
        self,
        base_url: str,
        endpoints: dict[str, str],
        *,
        request=None,
        resolve=None,
        timeout: float = 30.0,
    ):
        self.base_url = str(base_url or "").rstrip("/") + "/"
        self.endpoints = {key: str(value) for key, value in endpoints.items() if value}
        missing = sorted(self.REQUIRED_ENDPOINTS - self.endpoints.keys())
        if not self.base_url.strip("/") or missing:
            raise ValueError(f"Bilibili transport configuration is incomplete: {', '.join(missing)}")
        self.base_origin = _origin(self.base_url)
        self.request = request or self._request_json
        self.resolve = resolve or socket.getaddrinfo
        self.opener = build_opener(_NoRedirectHandler)
        self.timeout = max(1.0, float(timeout))

    def validate(self, token: str, client_id: str) -> dict[str, Any]:
        return self._call("POST", self.endpoints["validate"], token, {"client_id": client_id})

    def create_upload(
        self,
        token: str,
        metadata: dict[str, Any],
        total_bytes: int,
        previous_url: str | None = None,
    ) -> dict[str, Any]:
        if previous_url:
            return {"upload_url": previous_url, "upload_id": metadata.get("upload_id")}
        return self._call(
            "POST",
            self.endpoints["create_upload"],
            token,
            {"total_bytes": int(total_bytes), "metadata": metadata},
        )

    def upload_chunk(
        self,
        token: str,
        session: dict[str, Any],
        chunk: bytes,
        offset: int,
        total_bytes: int,
    ) -> int:
        raw_upload_url = str(session.get("upload_url") or "")
        if not raw_upload_url:
            raise RuntimeError("Bilibili upload URL is missing")
        upload_url = urljoin(self.base_url, raw_upload_url)
        try:
            upload_origin = _origin(upload_url)
        except ValueError as exc:
            raise RuntimeError("Bilibili upload URL must be HTTP(S)") from exc
        same_origin = self.base_origin == upload_origin
        if not same_origin:
            self._validate_public_upload_url(upload_url)
        headers = self._headers(token, include_authorization=same_origin)
        headers["Content-Type"] = "application/octet-stream"
        headers["Content-Range"] = f"bytes {offset}-{offset + len(chunk) - 1}/{total_bytes}"
        result = self.request("PUT", upload_url, headers, bytes(chunk))
        return int(result.get("next_offset", offset + len(chunk)))

    def complete_upload(self, token: str, session: dict[str, Any]) -> dict[str, Any]:
        return self._call(
            "POST",
            self.endpoints["complete_upload"],
            token,
            {"upload_id": session.get("upload_id"), "upload_url": session.get("upload_url")},
        )

    def publish(
        self, token: str, video_id: str, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        return self._call(
            "POST",
            self.endpoints["publish"],
            token,
            {"video_id": video_id, "metadata": metadata},
        )

    def query(self, token: str, remote_id: str) -> dict[str, Any]:
        path = self.endpoints["query"].format(remote_id=quote(remote_id, safe=""))
        return self._call("GET", path, token, None)

    def _call(
        self, method: str, path: str, token: str, payload: dict[str, Any] | None
    ) -> dict[str, Any]:
        url = path if path.startswith(("http://", "https://")) else urljoin(self.base_url, path.lstrip("/"))
        try:
            endpoint_origin = _origin(url)
        except ValueError as exc:
            raise RuntimeError("Bilibili endpoint must be HTTP(S)") from exc
        if endpoint_origin != self.base_origin:
            raise RuntimeError("Bilibili control endpoint must remain on the configured origin")
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return self.request(method, url, self._headers(token), body)

    def _validate_public_upload_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise RuntimeError("cross-origin Bilibili upload URL must use HTTPS")
        try:
            addresses = self.resolve(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise RuntimeError("Bilibili upload URL could not be resolved") from exc
        if not addresses:
            raise RuntimeError("Bilibili upload URL could not be resolved")
        for *_, sockaddr in addresses:
            try:
                address = ipaddress.ip_address(sockaddr[0])
            except ValueError as exc:
                raise RuntimeError("Bilibili upload URL returned an invalid address") from exc
            if not address.is_global:
                raise RuntimeError("cross-origin Bilibili upload URL must resolve to a public address")

    @staticmethod
    def _headers(token: str, *, include_authorization: bool = True) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
        }
        if include_authorization:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _request_json(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> dict[str, Any]:
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            raise RuntimeError(f"Bilibili API returned HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"Bilibili API request failed: {exc.reason}") from exc
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except ValueError as exc:
            raise RuntimeError("Bilibili API returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Bilibili API returned an invalid response")
        return payload


class BilibiliProvider:
    """Bilibili connector with resumable chunk upload and injected official transport."""

    def __init__(self, credentials: CredentialStore, transport: Any, *, chunk_size: int = 4 * 1024 * 1024):
        self.credentials = credentials
        self.transport = transport
        self.chunk_size = max(1, int(chunk_size))

    def validate(self, attempt: dict[str, Any]) -> dict[str, Any]:
        credential = self._credential(attempt)
        result = self.transport.validate(
            credential["access_token"], credential["client_id"]
        )
        if not result or not result.get("can_publish", False):
            raise PermissionError("Bilibili publish permission is unavailable")
        return result

    def upload(self, attempt: dict[str, Any], progress) -> dict[str, Any]:
        credential = self._credential(attempt)
        token = credential["access_token"]
        payload = attempt.get("payload") if isinstance(attempt.get("payload"), dict) else {}
        video_path = Path(str(payload.get("video_path") or ""))
        if not video_path.is_file():
            raise RuntimeError("publish video file is missing")
        total_bytes = video_path.stat().st_size
        offset = min(max(0, int(attempt.get("uploaded_bytes") or 0)), total_bytes)
        session = self.transport.create_upload(
            token,
            payload,
            total_bytes,
            previous_url=attempt.get("upload_url"),
        )
        upload_url = session.get("upload_url")
        with video_path.open("rb") as handle:
            handle.seek(offset)
            while offset < total_bytes:
                chunk = handle.read(min(self.chunk_size, total_bytes - offset))
                if not chunk:
                    raise RuntimeError("video upload ended before the expected size")
                next_offset = int(
                    self.transport.upload_chunk(
                        token, session, chunk, offset, total_bytes
                    )
                )
                if next_offset <= offset or next_offset > total_bytes:
                    raise RuntimeError("Bilibili upload returned an invalid offset")
                offset = next_offset
                progress(offset, upload_url)
        completed = self.transport.complete_upload(token, session)
        video_id = completed.get("video_id")
        if not video_id:
            raise RuntimeError("Bilibili upload did not return a video id")
        return self.transport.publish(token, video_id, payload)

    def query(self, attempt: dict[str, Any]) -> dict[str, Any]:
        credential = self._credential(attempt)
        remote_id = str(attempt.get("remote_id") or "").strip()
        if not remote_id:
            raise RuntimeError("Bilibili remote id is missing")
        return self.transport.query(credential["access_token"], remote_id)

    def _credential(self, attempt: dict[str, Any]) -> dict[str, str]:
        reference = str(attempt.get("credential_ref") or "").strip()
        if not reference:
            raise PermissionError("Bilibili authorization is not configured")
        raw = self.credentials.get(reference)
        if not raw:
            raise PermissionError("Bilibili authorization is not configured")
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise PermissionError("Bilibili authorization is invalid") from exc
        token = str(payload.get("access_token") or "").strip()
        client_id = str(payload.get("client_id") or "").strip()
        if not token or not client_id:
            raise PermissionError("Bilibili authorization is incomplete")
        return {"access_token": token, "client_id": client_id}
