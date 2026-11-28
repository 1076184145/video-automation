from __future__ import annotations

import errno
import sys
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


class DesktopWindowTests(unittest.TestCase):
    def _open_window_on(self, platform: str, allow_downloads: bool) -> list[tuple[str, bool]]:
        """Open the window with a fake pywebview; record ALLOW_DOWNLOADS when the window is created and started."""
        calls = []
        webview = SimpleNamespace(settings={"ALLOW_DOWNLOADS": allow_downloads})

        def create_window(*_args, **_kwargs):
            calls.append(("create_window", webview.settings["ALLOW_DOWNLOADS"]))
            return SimpleNamespace(events=SimpleNamespace(closed=None))

        def start():
            calls.append(("start", webview.settings["ALLOW_DOWNLOADS"]))

        webview.create_window, webview.start = create_window, start

        with (
            patch.dict(sys.modules, {"webview": webview}),
            patch.object(desktop_app.sys, "platform", platform),
            patch.object(desktop_app, "_stop_server"),
        ):
            desktop_app._open_window("http://127.0.0.1:8765/#/", None)
        return calls

    def test_windows_allows_downloads_before_the_window_is_created_and_started(self) -> None:
        self.assertEqual(self._open_window_on("win32", False), [("create_window", True), ("start", True)])

    def test_macos_and_linux_keep_the_existing_download_setting(self) -> None:
        for platform in ("darwin", "linux"):
            for existing in (False, True):
                with self.subTest(platform=platform, existing=existing):
                    self.assertEqual(
                        self._open_window_on(platform, existing), [("create_window", existing), ("start", existing)]
                    )


if __name__ == "__main__":
    unittest.main()
