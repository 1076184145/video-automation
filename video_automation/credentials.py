from __future__ import annotations

from typing import Protocol


class CredentialStoreUnavailable(RuntimeError):
    pass


class CredentialStore(Protocol):
    def get(self, reference: str) -> str | None: ...
    def set(self, reference: str, secret: str) -> None: ...
    def delete(self, reference: str) -> None: ...


class SystemCredentialStore:
    """Stores publish credentials through the operating-system credential backend."""

    def __init__(self, service_name: str = "video-automation"):
        self.service_name = service_name

    @staticmethod
    def _keyring():
        try:
            import keyring  # type: ignore
        except ImportError as exc:
            raise CredentialStoreUnavailable(
                "Install the keyring package to use the operating-system credential store"
            ) from exc
        return keyring

    def get(self, reference: str) -> str | None:
        return self._keyring().get_password(self.service_name, reference)

    def set(self, reference: str, secret: str) -> None:
        self._keyring().set_password(self.service_name, reference, secret)

    def delete(self, reference: str) -> None:
        try:
            self._keyring().delete_password(self.service_name, reference)
        except Exception as exc:
            if exc.__class__.__name__ != "PasswordDeleteError":
                raise


class MemoryCredentialStore:
    """Volatile credential store for tests and explicitly ephemeral sessions."""

    def __init__(self):
        self._values: dict[str, str] = {}

    def get(self, reference: str) -> str | None:
        return self._values.get(reference)

    def set(self, reference: str, secret: str) -> None:
        self._values[reference] = secret

    def delete(self, reference: str) -> None:
        self._values.pop(reference, None)
