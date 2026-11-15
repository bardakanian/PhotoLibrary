"""System notifications ("Thumbnails ready") for jobs long enough that you've likely looked away."""

import logging
import sys

from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from ..core.media import spawn_tool

LOGGER = logging.getLogger(__name__)
_tray: QSystemTrayIcon | None = None


def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def notify(title: str, message: str) -> None:
    """Show a notification in Notification Center (macOS) or as a tray message (Windows, Linux)."""
    if sys.platform == "darwin":
        # Without an app bundle of its own, Python can't post to Notification Center directly.
        script = f"display notification {_applescript_string(message)} with title {_applescript_string(title)}"
        try:
            spawn_tool(["osascript", "-e", script])  # fork-free, like every helper program here
        except OSError:
            LOGGER.exception("Could not show a notification")
        return
    global _tray
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return
    if _tray is None:
        app = QApplication.instance()
        _tray = QSystemTrayIcon(app.windowIcon(), app)
        _tray.setToolTip(app.applicationDisplayName())
    _tray.show()  # a tray message needs a visible tray icon
    _tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 8000)
