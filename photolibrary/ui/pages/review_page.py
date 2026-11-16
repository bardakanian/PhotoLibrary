"""Review People: "Is this Ana?" for faces that weren't grouped with anyone.

One at a time: Yes locks the face to the person; No is remembered, so the face is never suggested
for (or regrouped into) that person again. Skip leaves it for later. For many at once, Accept
Likely Matches takes every close match, and Check by Person shows one person's suggestions as a
grid, all ticked: untick the wrong ones, then accept.
"""

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPainterPath, QPen, QShortcut, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListView, QMenu, QPushButton, QStackedWidget, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from ...core.models import FaceSuggestion
from ...core.people import LIKELY_MATCH
from ..thumbnails import ThumbnailLoader
from ..widgets.common import EmptyState, PageHeader
from ..widgets.face_view import FaceView, face_source_rect

SuggestionRole = Qt.ItemDataRole.UserRole + 1
GRID_BUCKET = 384  # faces are cut from this thumbnail size: sharp enough for a 104 px tile
TILE = 104


class FaceTileDelegate(QStyledItemDelegate):
    """A suggested face, round, with a tick when it will be accepted."""

    def __init__(self, loader: ThumbnailLoader, theme, parent=None):
        super().__init__(parent)
        self.loader, self.theme = loader, theme

    def sizeHint(self, option, index) -> QSize:
        return QSize(TILE + 8, TILE + 8)

    def editorEvent(self, event, model, option, index) -> bool:
        # The page toggles a tile on any click. Qt would also toggle the (undrawn) checkbox near the
        # tile's left edge, and the two toggles cancelled out: such clicks did nothing.
        return False

    def paint(self, painter: QPainter, option, index) -> None:
        suggestion: FaceSuggestion = index.data(SuggestionRole)
        ticked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked.value
        colors = self.theme.colors
        rect = QRectF(option.rect).adjusted(4, 4, -4, -4)
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            path = QPainterPath()
            path.addRoundedRect(rect, 10, 10)
            painter.setClipPath(path)
            pixmap = self.loader.pixmap(suggestion.item, GRID_BUCKET)
            if not self.loader.has_exact(suggestion.item, GRID_BUCKET):
                self.loader.request(suggestion.item, GRID_BUCKET)
            if pixmap is not None:
                painter.drawPixmap(rect, pixmap, face_source_rect(pixmap.width(), pixmap.height(), suggestion.box))
            else:
                painter.fillRect(rect, QColor(colors.border))
            if not ticked:
                painter.fillRect(rect, QColor(0, 0, 0, 150))  # left out: dimmed
            painter.setClipping(False)
            mark = QRectF(rect.right() - 26, rect.bottom() - 26, 20, 20)
            painter.setPen(QPen(QColor(255, 255, 255), 1.5))
            painter.setBrush(QColor(colors.accent) if ticked else QColor(0, 0, 0, 90))
            painter.drawEllipse(mark)
            if ticked:
                pen = QPen(QColor(255, 255, 255), 2)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                c = mark.center()
                painter.drawPolyline([QPointF(c.x() - 4.5, c.y()), QPointF(c.x() - 1.2, c.y() + 3.3),
                                      QPointF(c.x() + 4.8, c.y() - 3.2)])
            if not ticked:
                painter.setPen(QColor(255, 255, 255))
                painter.drawText(rect.adjusted(0, 0, 0, -28), Qt.AlignmentFlag.AlignCenter, "Not them")
        finally:
            painter.restore()

class ReviewPage(QWidget):
    back_requested = Signal()
    answered = Signal(bool, object)  # yes?, FaceSuggestion
    accept_likely_requested = Signal(object)   # the close matches (list of FaceSuggestion)
    person_answered = Signal(object, object)   # accepted, rejected (lists of FaceSuggestion)

    def __init__(self, loader: ThumbnailLoader, theme, parent=None):
        super().__init__(parent)
        self.loader, self.theme = loader, theme
        self.grid_person: int | None = None
        self.suggestions: list[FaceSuggestion] = []
        self.covers: dict[int, tuple] = {}  # person id -> (cover item, face box)
        self.index = 0
        self.answers = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 24)
        outer.setSpacing(18)
        self.header = PageHeader("Review People", "Faces that look like someone you know. Your answers stay.")
        self.likely_button = QPushButton("Accept Likely Matches…")
        self.likely_button.setToolTip(f"Accept every suggestion at least {LIKELY_MATCH:.0%} alike: as alike as a "
                                      "person's own photos usually are. You see the numbers first.")
        self.likely_button.clicked.connect(lambda: self.accept_likely_requested.emit(self.likely_matches()))
        self.header.actions.addWidget(self.likely_button)
        self.person_button = QPushButton("Check by Person")
        self.person_button.setToolTip("See one person's suggestions together, untick the wrong ones, accept the rest")
        self.person_menu = QMenu(self.person_button)
        self.person_menu.aboutToShow.connect(self._fill_person_menu)
        self.person_button.setMenu(self.person_menu)
        self.header.actions.addWidget(self.person_button)
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
        self.stack.addWidget(self._build_person_grid())
        outer.addWidget(self.stack, 1)

        self.yes_button.clicked.connect(lambda: self.answer(True))
        self.no_button.clicked.connect(lambda: self.answer(False))
        self.skip_button.clicked.connect(self.skip)
        for keys, slot in (("Y", lambda: self.answer(True)), ("N", lambda: self.answer(False)),
                           ("Right", self.skip), ("S", self.skip), ("Esc", self._escape)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _build_person_grid(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.grid_title = QLabel()
        self.grid_title.setProperty("role", "section")
        self.grid_note = QLabel("Ticked faces are added to them. Click a face to untick it if it isn't them; "
                                "unticked faces are remembered as not them.")
        self.grid_note.setProperty("muted", True)
        self.grid_note.setWordWrap(True)
        layout.addWidget(self.grid_title)
        layout.addWidget(self.grid_note)
        self.grid_model = QStandardItemModel(self)
        self.grid = QListView()
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setSpacing(2)
        self.grid.setSelectionMode(QListView.SelectionMode.NoSelection)
        self.grid.setModel(self.grid_model)
        self.grid.setItemDelegate(FaceTileDelegate(self.loader, self.theme, self.grid))
        self.grid.clicked.connect(self._toggle_tile)
        self.loader.updated.connect(lambda _item_id: self.grid.viewport().update())
        layout.addWidget(self.grid, 1)
        buttons = QHBoxLayout()
        self.tick_all_button = QPushButton("Tick All")
        self.tick_all_button.clicked.connect(lambda: self._tick_all(True))
        self.grid_cancel_button = QPushButton("Cancel")
        self.grid_cancel_button.clicked.connect(self._show_current)
        self.grid_accept_button = QPushButton()
        self.grid_accept_button.setProperty("variant", "primary")
        self.grid_accept_button.clicked.connect(self._accept_person)
        buttons.addWidget(self.tick_all_button)
        buttons.addStretch(1)
        buttons.addWidget(self.grid_cancel_button)
        buttons.addWidget(self.grid_accept_button)
        layout.addLayout(buttons)
        self.grid_model.itemChanged.connect(lambda _item: self._update_grid_buttons())
        return page

    def likely_matches(self) -> list[FaceSuggestion]:
        """Unanswered suggestions at least LIKELY_MATCH alike (from where the one-at-a-time review is)."""
        return [entry for entry in self.suggestions[self.index:] if entry.similarity >= LIKELY_MATCH]

    def remaining_by_person(self) -> list[tuple[int, str, int]]:
        """(person id, name, unanswered suggestions), most first."""
        counts: dict[int, list] = {}
        for entry in self.suggestions[self.index:]:
            counts.setdefault(entry.person_id, [entry.name, 0])[1] += 1
        return sorted(((person, name, count) for person, (name, count) in counts.items()), key=lambda row: -row[2])

    def _fill_person_menu(self) -> None:
        self.person_menu.clear()
        rows = self.remaining_by_person()
        for person, name, count in rows:
            self.person_menu.addAction(f"{name or 'Unnamed person'}  ({count:,})",
                                       lambda person=person: self.show_person(person))
        if not rows:
            self.person_menu.addAction("Nothing left to review").setEnabled(False)

    def show_person(self, person_id: int) -> None:
        """One person's unanswered suggestions as a grid, all ticked, closest first."""
        entries = sorted((entry for entry in self.suggestions[self.index:] if entry.person_id == person_id),
                         key=lambda entry: -entry.similarity)
        if not entries:
            return
        self.grid_person = person_id
        self.grid_title.setText(f"Are these {entries[0].name}?" if entries[0].name else "Are these the same person?")
        self.grid_model.clear()
        for entry in entries:
            tile = QStandardItem()
            tile.setData(entry, SuggestionRole)
            tile.setToolTip(f"{entry.item.name}  ·  {entry.similarity:.0%} alike")
            tile.setCheckable(True)
            tile.setCheckState(Qt.CheckState.Checked)
            tile.setEditable(False)
            self.grid_model.appendRow(tile)
        self._update_grid_buttons()
        self.stack.setCurrentIndex(2)

    def _toggle_tile(self, index) -> None:
        tile = self.grid_model.itemFromIndex(index)
        tile.setCheckState(Qt.CheckState.Unchecked if tile.checkState() == Qt.CheckState.Checked
                           else Qt.CheckState.Checked)

    def _tick_all(self, ticked: bool) -> None:
        for row in range(self.grid_model.rowCount()):
            self.grid_model.item(row).setCheckState(Qt.CheckState.Checked if ticked else Qt.CheckState.Unchecked)

    def grid_answers(self) -> tuple[list[FaceSuggestion], list[FaceSuggestion]]:
        accepted, rejected = [], []
        for row in range(self.grid_model.rowCount()):
            tile = self.grid_model.item(row)
            (accepted if tile.checkState() == Qt.CheckState.Checked else rejected).append(tile.data(SuggestionRole))
        return accepted, rejected

    def _update_grid_buttons(self) -> None:
        accepted, rejected = self.grid_answers()
        text = f"Add {len(accepted):,}"
        if rejected:
            text += f", Not Them {len(rejected):,}"
        self.grid_accept_button.setText(text)

    def _escape(self) -> None:
        """Esc leaves a person's grid (nothing saved) for the one-at-a-time view; there, it leaves Review."""
        if self.stack.currentIndex() == 2:
            self._show_current()
        else:
            self.back_requested.emit()

    def go_on(self) -> None:
        """Show what's left after answers given in bulk."""
        self._show_current()

    def _accept_person(self) -> None:
        accepted, rejected = self.grid_answers()
        self.forget([entry.face_id for entry in accepted + rejected])
        self.answers += len(accepted) + len(rejected)
        self.person_answered.emit(accepted, rejected)
        self._show_current()

    def forget(self, face_ids) -> None:
        """Take answered faces out of the list (answered in bulk, not one at a time)."""
        gone = set(face_ids)
        before = self.suggestions[:self.index]
        after = [entry for entry in self.suggestions[self.index:] if entry.face_id not in gone]
        self.suggestions = before + after

    @property
    def current(self) -> FaceSuggestion | None:
        return self.suggestions[self.index] if 0 <= self.index < len(self.suggestions) else None

    def show_loading(self) -> None:
        self.suggestions, self.index = [], 0
        self.likely_button.setEnabled(False)
        self.person_button.setEnabled(False)
        self.empty.set_content("Loading suggestions…", "")
        self.stack.setCurrentIndex(0)

    def set_suggestions(self, suggestions: list[FaceSuggestion], covers: dict[int, tuple]) -> None:
        self.suggestions, self.covers, self.index, self.answers = list(suggestions), covers, 0, 0
        self._show_current()

    @property
    def showing_card(self) -> bool:
        """The one-at-a-time question is on screen (Y, N and → only answer it then)."""
        return self.stack.currentIndex() == 1

    def answer(self, yes: bool) -> None:
        suggestion = self.current
        if suggestion is None or not self.showing_card:
            return
        self.answers += 1
        self.answered.emit(yes, suggestion)
        self.index += 1
        self._show_current()

    def skip(self) -> None:
        if self.current is not None and self.showing_card:
            self.index += 1
            self._show_current()

    def _show_current(self) -> None:
        left = len(self.suggestions) - self.index
        likely = len(self.likely_matches())
        self.likely_button.setText(f"Accept Likely Matches ({likely:,})…")
        self.likely_button.setEnabled(bool(likely))
        self.person_button.setEnabled(left > 0)
        suggestion = self.current
        if suggestion is None:
            title = "All caught up" if self.answers or self.suggestions else "No suggestions right now"
            message = ("Every face that looks like someone has been reviewed."
                       if self.suggestions else
                       "Faces that look like someone you've named, but weren't grouped with them, appear "
                       "here. Name people in the sidebar to get suggestions for them.")
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
