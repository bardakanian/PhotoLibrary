from PySide6.QtCore import QObject, QEvent, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from .colors import DARK, LIGHT, Palette
from .stylesheet import build_stylesheet


class ThemeManager(QObject):
    changed = Signal(str)

    def __init__(self, app: QApplication, preference: str = "system"):
        super().__init__(app)
        self.app = app
        self.preference = preference
        self._applied: str | None = None  # "light" or "dark" currently in the stylesheet
        app.installEventFilter(self)
        self.apply(preference)

    def _system_is_dark(self) -> bool:
        return self.app.palette().color(QPalette.ColorRole.Window).lightness() < 128

    @property
    def resolved(self) -> str:
        return ("dark" if self._system_is_dark() else "light") if self.preference == "system" else self.preference

    @property
    def colors(self) -> Palette:
        return DARK if self.resolved == "dark" else LIGHT

    def apply(self, preference: str) -> None:
        if preference not in {"system", "light", "dark"}:
            preference = "system"
        self.preference = preference
        self._applied = self.resolved
        self.app.setStyleSheet(build_stylesheet(self.colors))
        self.changed.emit(preference)

    def eventFilter(self, watched, event):
        # Windows reports palette changes for many reasons (accent colour, Night light, display
        # changes), and the event reaches this filter once per widget. Re-applying the stylesheet
        # re-styles the whole window (most of a second), so do it only when light/dark flipped.
        if (event.type() == QEvent.Type.ApplicationPaletteChange and watched is self.app
                and self.preference == "system" and self.resolved != self._applied):
            self._applied = self.resolved
            self.app.setStyleSheet(build_stylesheet(self.colors))
            self.changed.emit(self.preference)
        return super().eventFilter(watched, event)
