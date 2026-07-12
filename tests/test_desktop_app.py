from __future__ import annotations

import errno
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import desktop_app


class DesktopAppServerTests(unittest.TestCase):
    def test_occupied_api_port_is_not_treated_as_the_expected_service(self) -> None:
        settings = SimpleNamespace(api_port=8765)
        error = OSError(errno.EADDRINUSE, "address in use")

        with patch.object(desktop_app, "create_server", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "unverified local service"):
                desktop_app._start_server(settings)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
