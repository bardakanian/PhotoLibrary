from unittest.mock import patch

from PySide6.QtGui import QColor, QPalette

from photolibrary.ui.theme import manager

from .base import WindowTestCase


class SystemThemeTests(WindowTestCase):
    """The app follows the system theme without re-styling the window for unrelated palette changes."""

    def setUp(self):
        super().setUp()
        # Managers of earlier test windows belong to the shared QApplication and still listen.
        for leftover in self.app.findChildren(manager.ThemeManager):
            if leftover is not self.window.theme:
                self.app.removeEventFilter(leftover)
        original = QPalette(self.app.palette())
        self.addCleanup(lambda: (self.app.setPalette(original), self.settle(0.05)))
        self.rebuilds = 0
        real_build = manager.build_stylesheet

        def counting(colors):
            self.rebuilds += 1
            return real_build(colors)

        patcher = patch.object(manager, "build_stylesheet", side_effect=counting)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.changes = []
        self.window.theme.changed.connect(self.changes.append)

    def set_palette(self, role: QPalette.ColorRole, colour: QColor) -> None:
        palette = QPalette(self.app.palette())
        palette.setColor(role, colour)
        self.app.setPalette(palette)
        self.settle(0.05)

    def test_accent_colour_change_does_not_restyle(self):
        accent = self.app.palette().color(QPalette.ColorRole.Highlight)
        self.set_palette(QPalette.ColorRole.Highlight, accent.lighter(110))
        self.assertEqual((self.rebuilds, self.changes), (0, []))

    def test_light_dark_switch_restyles_once(self):
        was_dark = self.window.theme.resolved == "dark"
        self.set_palette(QPalette.ColorRole.Window, QColor(240, 240, 240) if was_dark else QColor(30, 30, 30))
        self.assertEqual(self.window.theme.resolved, "light" if was_dark else "dark")
        self.assertEqual((self.rebuilds, self.changes), (1, ["system"]))
