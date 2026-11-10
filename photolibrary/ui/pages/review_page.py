"""Review People: "Is this Ana?" for faces that weren't grouped with anyone, one at a time.

Yes locks the face to the person; No is remembered, so the face is never suggested for (or
regrouped into) that person again. Skip leaves it for later.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from ...core.models import FaceSuggestion
from ..thumbnails import ThumbnailLoader
from ..widgets.common import EmptyState, PageHeader
from ..widgets.face_view import FaceView

class ReviewPage(QWidget):
    back_requested = Signal()
    answered = Signal(bool, object)  # yes?, FaceSuggestion

    def __init__(self, loader: ThumbnailLoader, theme, parent=None):
        super().__init__(parent)
        self.suggestions: list[FaceSuggestion] = []
        self.covers: dict[int, tuple] = {}  # person id -> (cover item, face box)
        self.index = 0
        self.answers = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 24)
        outer.setSpacing(18)
        self.header = PageHeader("Review People", "Faces that look like someone you know. Your answers stay.")
        back = QPushButton("‹  Library")
        back.setToolTip("Back to the library (Esc)")
        back.clicked.connect(self.back_requested)
        self.header.actions.addWidget(back)
        outer.addWidget(self.header)

        self.stack = QStackedWidget()
        self.empty = EmptyState("Loading suggestions…", "")
        empty_page = QWidget()
        empty_layout = QVBoxLayout(empty_page)
        empty_layout.addStretch(1)
        empty_layout.addWidget(self.empty, alignment=Qt.AlignmentFlag.AlignCenter)
        empty_layout.addStretch(2)
        self.empty.action_requested.connect(self.back_requested)
        self.stack.addWidget(empty_page)

        card = QWidget()
        layout = QVBoxLayout(card)
        layout.setSpacing(16)
        layout.addStretch(1)
        faces = QHBoxLayout()
        faces.setSpacing(36)
        faces.addStretch(1)
        self.face = FaceView(loader, theme, 220)
        self.person_face = FaceView(loader, theme, 140)
        faces.addWidget(self.face)
        faces.addWidget(self.person_face, alignment=Qt.AlignmentFlag.AlignVCenter)
        faces.addStretch(1)
        layout.addLayout(faces)
        self.question = QLabel()
        self.question.setProperty("role", "section")
        self.question.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.question)
        self.detail = QLabel()
        self.detail.setProperty("muted", True)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.detail)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.yes_button = QPushButton("Yes  (Y)")
        self.yes_button.setProperty("variant", "primary")
        self.no_button = QPushButton("No  (N)")
        self.skip_button = QPushButton("Skip  (→)")
        for button in (self.yes_button, self.no_button, self.skip_button):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.progress = QLabel()
        self.progress.setProperty("subtle", True)
        self.progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.progress)
        layout.addStretch(2)
        self.stack.addWidget(card)
        self.card = card
        outer.addWidget(self.stack, 1)

        self.yes_button.clicked.connect(lambda: self.answer(True))
        self.no_button.clicked.connect(lambda: self.answer(False))
        self.skip_button.clicked.connect(self.skip)
        for keys, slot in (("Y", lambda: self.answer(True)), ("N", lambda: self.answer(False)),
                           ("Right", self.skip), ("S", self.skip), ("Esc", self.back_requested.emit)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    @property
    def current(self) -> FaceSuggestion | None:
        return self.suggestions[self.index] if 0 <= self.index < len(self.suggestions) else None

    def show_loading(self) -> None:
        self.suggestions, self.index = [], 0
        self.empty.set_content("Loading suggestions…", "")
        self.stack.setCurrentIndex(0)

    def set_suggestions(self, suggestions: list[FaceSuggestion], covers: dict[int, tuple]) -> None:
        self.suggestions, self.covers, self.index, self.answers = list(suggestions), covers, 0, 0
        self._show_current()

    def answer(self, yes: bool) -> None:
        suggestion = self.current
        if suggestion is None:
            return
        self.answers += 1
        self.answered.emit(yes, suggestion)
        self.index += 1
        self._show_current()

    def skip(self) -> None:
        if self.current is not None:
            self.index += 1
            self._show_current()

    def _show_current(self) -> None:
        suggestion = self.current
        if suggestion is None:
            title = "All caught up" if self.answers or self.suggestions else "No suggestions right now"
            message = ("Every face that looks like someone has been reviewed."
                       if self.suggestions else
                       "Faces appear here when they look like a person but weren't grouped with them.")
            self.empty.set_content(title, message, "Back to Library")
            self.stack.setCurrentIndex(0)
            return
        self.stack.setCurrentIndex(1)
        self.face.set_face(suggestion.item, suggestion.box)
        cover = self.covers.get(suggestion.person_id)
        self.person_face.set_face(*(cover if cover else (None, None)))
        self.person_face.setToolTip(suggestion.name or "Unnamed person")
        self.question.setText(f"Is this {suggestion.name}?" if suggestion.name else "Is this the same person?")
        self.detail.setText(suggestion.item.name)
        self.progress.setText(f"{self.index + 1:,} of {len(self.suggestions):,}")
