from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from ..grid_view import MediaGridView
from ..widgets import EmptyState


class GridPage(QStackedWidget):
    """The thumbnail grid, or an empty-state message when there is nothing to show."""

    empty_action_requested = Signal(str)  # the action key of the current empty state

    def __init__(self, view: MediaGridView, parent=None):
        super().__init__(parent)
        self.view = view
        view.setObjectName("mediaGrid")
        self.addWidget(view)
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(24, 24, 24, 24)
        self.empty = EmptyState("", "")
        layout.addStretch(1)
        layout.addWidget(self.empty, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(2)
        self.addWidget(holder)
        self._action = ""
        self.empty.action_requested.connect(lambda: self.empty_action_requested.emit(self._action))

    def show_grid(self) -> None:
        self.setCurrentIndex(0)

    def show_empty(self, title: str, message: str, action_text: str = "", action: str = "") -> None:
        self._action = action
        self.empty.set_content(title, message, action_text)
        self.setCurrentIndex(1)

    @property
    def showing_grid(self) -> bool:
        return self.currentIndex() == 0
