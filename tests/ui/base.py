import json
import os
import time
import unittest
from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ..base import TempAppTestCase


def application() -> QApplication:
    return QApplication.instance() or QApplication([])


class WindowTestCase(TempAppTestCase):
    """Starts the real MainWindow on a small generated library and waits until it is ready.

    Library: 12 photos in album 2026-1 (January), 12 in 2026-2 (February), 2 at the root
    (December 2025). Newest first, February items come first.
    """

    watch_folder = False  # FSEvents is exercised by its own tests

    @classmethod
    def setUpClass(cls):
        if os.environ.get("SKIP_UI_TESTS"):
            raise unittest.SkipTest("SKIP_UI_TESTS is set")
        super().setUpClass()

    def setUp(self):
        super().setUp()
        self.app = application()
        self.library = self.make_library("Library")
        start = datetime(2026, 1, 3, 9, 0)
        for index in range(12):
            self.write_photo(self.library / "2026-1" / f"JAN_{index:02d}.jpg", start + timedelta(days=index))
            self.write_photo(self.library / "2026-2" / f"FEB_{index:02d}.jpg", start + timedelta(days=31 + index))
        for index in range(2):
            self.write_photo(self.library / f"DEC_{index}.jpg", datetime(2025, 12, 20 + index, 18))
        self.expected_total = 26
        self.build_extra_library()
        self.app_dir.mkdir(parents=True, exist_ok=True)
        self.config_file.write_text(json.dumps({
            "library_folder": str(self.library), "view_mode": "all", "zoom": 120,
            "watch_folder": self.watch_folder, "info_panel_visible": False,
        }))
        from photolibrary.ui.main_window import MainWindow
        self.window = MainWindow(self.app)
        self.window.resize(1200, 800)
        self.window.show()
        self.wait_until(lambda: self.window.ready_text.startswith("Library ready") and not self.window.scanning
                        and len(self.window.model.items) == self.expected_total, timeout=20)
        self.settle()

    def build_extra_library(self) -> None:
        """Hook for subclasses that need more files before the window starts."""

    def tearDown(self):
        self.window.close()
        self.settle(0.2)
        self.window.deleteLater()
        self.settle(0.1)
        super().tearDown()

    # Helpers ----------------------------------------------------------------

    def settle(self, seconds: float = 0.3) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)

    def wait_until(self, condition, timeout: float = 10.0, message: str = "condition") -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            time.sleep(0.02)
        self.fail(f"Timed out waiting for {message}")

    @property
    def view(self):
        return self.window.view

    @property
    def names(self) -> list[str]:
        return [item.name for item in self.window.model.items]

    def click_row(self, row: int, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
        index = self.window.model.index(row, 0)
        self.view.scrollTo(index)
        self.settle(0.05)
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton, modifiers,
                         self.view.visualRect(index).center())
        self.settle(0.05)

    def selected_rows(self) -> list[int]:
        items = self.window.model.items
        return sorted(items.index(item) for item in self.window.selected_items())
