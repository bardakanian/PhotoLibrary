"""Sidebar listing the library views (All, Photos, Videos), the library folders and their albums."""

from pathlib import Path

from PySide6.QtCore import QModelIndex, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QAbstractItemView, QFrame, QMenu, QStyle, QStyledItemDelegate, QTreeView, QVBoxLayout

from ...core.library_roots import display_names
from ...core.models import Album, PeopleGroup, Person
from ...core.settings import SIDEBAR_SECTIONS
from ..theme.typography import Typography
from ..thumbnails import ThumbnailLoader
from .face_view import FACE_BUCKET, face_source_rect

ScopeRole = Qt.ItemDataRole.UserRole + 1   # ("kind", …), ("folder", root), ("album", root, name) or ("person", id)
CountRole = Qt.ItemDataRole.UserRole + 2
CoverRole = Qt.ItemDataRole.UserRole + 3   # MediaItem used as the album thumbnail
HeaderRole = Qt.ItemDataRole.UserRole + 4   # the section's key: "library", "folders", "albums" or "hidden"
CollapsedRole = Qt.ItemDataRole.UserRole + 5
FaceBoxRole = Qt.ItemDataRole.UserRole + 6   # a person's cover face (x, y, w, h) as fractions of the photo
ReviewRole = Qt.ItemDataRole.UserRole + 7  # the "Review Suggestions" row: opens the Review page
MoreRole = Qt.ItemDataRole.UserRole + 8    # the "Show N More…" / "Show Fewer" row at the end of People
# Unnamed people in fewer photos than this are folded away until "Show N More…" is clicked:
# mostly passers-by, and a real library has a hundred or more of them.
FEW_PHOTOS = 10
UNNAMED = "Unnamed person"

LIBRARY_ENTRIES = (("all", "All Photos"), ("photos", "Photos"), ("videos", "Videos"))
ICON_SIZE = 22
FACE_SIZE = 34      # people's face pictures: a face needs more room than a glyph to be recognised
FACE_ROW_HEIGHT = 42


class SidebarDelegate(QStyledItemDelegate):
    def __init__(self, loader: ThumbnailLoader, theme, parent=None):
        super().__init__(parent)
        self.loader = loader
        self.theme = theme
        self.header_font = QFont()
        self.header_font.setPixelSize(Typography.CAPTION)
        self.header_font.setWeight(QFont.Weight.Bold)
        self.count_font = QFont()
        self.count_font.setPixelSize(Typography.CAPTION)

    def sizeHint(self, option, index) -> QSize:
        if index.data(HeaderRole):
            return QSize(option.rect.width(), 28)
        return QSize(option.rect.width(), FACE_ROW_HEIGHT if index.data(FaceBoxRole) else 32)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        colors = self.theme.colors
        rect = option.rect
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if index.data(HeaderRole):
            painter.setFont(self.header_font)
            painter.setPen(QColor(colors.text_subtle))
            painter.drawText(rect.adjusted(12, 6, -8, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             index.data(Qt.ItemDataRole.DisplayRole).upper())
            if option.state & QStyle.StateFlag.State_MouseOver or index.data(CollapsedRole):
                self._paint_chevron(painter, rect, bool(index.data(CollapsedRole)))
            painter.restore()
            return

        background = QRectF(rect.adjusted(6, 1, -6, -1))
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors.accent_soft))
            painter.drawRoundedRect(background, 6, 6)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors.border))
            painter.drawRoundedRect(background, 6, 6)

        size = FACE_SIZE if index.data(FaceBoxRole) else ICON_SIZE
        icon = QRectF(rect.left() + (12 if size > ICON_SIZE else 14), rect.center().y() - size / 2 + 1, size, size)
        scope = index.data(ScopeRole)
        cover = index.data(CoverRole)
        if index.data(MoreRole):
            painter.setPen(QColor(colors.accent))
            painter.drawText(rect.adjusted(int(icon.left()) - rect.left(), 0, -14, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, index.data(Qt.ItemDataRole.DisplayRole))
            painter.restore()
            return
        if index.data(ReviewRole):
            self._paint_question(painter, icon)
        elif cover is not None:
            self._paint_cover(painter, icon, cover, index.data(FaceBoxRole))
        else:
            self._paint_glyph(painter, icon, scope)

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        count = index.data(CountRole)
        count_text = f"{count:,}" if count is not None else ""
        painter.setFont(self.count_font)
        count_width = painter.fontMetrics().horizontalAdvance(count_text)
        painter.setPen(QColor(colors.text_muted))
        text_rect = rect.adjusted(int(icon.right()) - rect.left() + 9, 0, -14, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, count_text)
        painter.setFont(option.font)
        painter.setPen(QColor(colors.accent if selected else colors.text))
        name_rect = text_rect.adjusted(0, 0, -(count_width + 8), 0)
        name = painter.fontMetrics().elidedText(index.data(Qt.ItemDataRole.DisplayRole), Qt.TextElideMode.ElideRight,
                                                name_rect.width())
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        painter.restore()

    def _paint_question(self, painter: QPainter, rect: QRectF) -> None:
        pen = QPen(QColor(self.theme.colors.accent), 1.6)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect.adjusted(3, 3, -3, -3))
        painter.setFont(self.header_font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "?")

    def _paint_chevron(self, painter: QPainter, rect, collapsed: bool) -> None:
        """A small › (collapsed) or ⌄ (open) at the right of a section header, as in Finder."""
        pen = QPen(QColor(self.theme.colors.text_subtle), 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        x, y = rect.right() - 18, rect.center().y() + 3
        if collapsed:
            points = [QPointF(x - 2, y - 4), QPointF(x + 2, y), QPointF(x - 2, y + 4)]
        else:
            points = [QPointF(x - 4, y - 2), QPointF(x, y + 2), QPointF(x + 4, y - 2)]
        painter.drawPolyline(points)

    def _paint_cover(self, painter: QPainter, rect: QRectF, cover, face=None) -> None:
        """An album's newest photo, or for a person their face, cut from the grid thumbnail and round."""
        path = QPainterPath()
        if face:
            path.addEllipse(rect)
        else:
            path.addRoundedRect(rect, 5, 5)
        # A face is a small part of the photo, so it is cut from the largest grid thumbnail.
        bucket = FACE_BUCKET if face else 96
        pixmap = self.loader.pixmap(cover, bucket)
        if not self.loader.has_exact(cover, bucket):
            self.loader.request(cover, bucket)
        painter.save()
        painter.setClipPath(path)
        if pixmap is not None:
            side = min(pixmap.width(), pixmap.height())
            source = QRectF((pixmap.width() - side) / 2, (pixmap.height() - side) / 2, side, side)
            if face:
                source = face_source_rect(pixmap.width(), pixmap.height(), face)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(rect, pixmap, source)
        else:
            painter.fillRect(rect, QColor(self.theme.colors.border))
        painter.restore()

    def _paint_glyph(self, painter: QPainter, rect: QRectF, scope) -> None:
        """Small line icons for the library entries, drawn so they follow the theme colours."""
        colors = self.theme.colors
        pen = QPen(QColor(colors.accent), 1.6)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        inner = rect.adjusted(3, 4, -3, -4)
        kind = scope[1] if scope and scope[0] == "kind" else (scope[0] if scope else "all")
        if kind == "group":
            # Two heads and shoulders, one behind the other.
            for dx, scale in ((3.0, 0.8), (-2.0, 1.0)):
                centre_x = inner.center().x() + dx
                head = 3.2 * scale
                painter.drawEllipse(QPointF(centre_x, inner.top() + head + 0.5), head, head)
                painter.drawArc(QRectF(centre_x - 5.5 * scale, inner.top() + 2 * head + 2, 11 * scale, 9 * scale),
                                0, 180 * 16)
        elif kind == "folder":
            # A folder outline with a tab.
            tab = QRectF(inner.left(), inner.top(), inner.width() * 0.42, 3)
            painter.drawPolyline([QPointF(inner.left(), inner.bottom()), QPointF(inner.left(), tab.top()),
                                  QPointF(tab.right(), tab.top()), QPointF(tab.right() + 2, tab.top() + 3),
                                  QPointF(inner.right(), tab.top() + 3), QPointF(inner.right(), inner.bottom()),
                                  QPointF(inner.left(), inner.bottom())])
        elif kind == "videos":
            body = QRectF(inner.left(), inner.top() + 1, inner.width() - 6, inner.height() - 2)
            painter.drawRoundedRect(body, 2.5, 2.5)
            painter.drawPolyline([QPointF(body.right(), body.center().y() - 1.5), QPointF(inner.right(), inner.top() + 2),
                                  QPointF(inner.right(), inner.bottom() - 2), QPointF(body.right(), body.center().y() + 1.5)])
        elif kind == "photos":
            painter.drawRoundedRect(inner, 2.5, 2.5)
            painter.drawEllipse(QRectF(inner.left() + 3, inner.top() + 3, 3.5, 3.5))
            painter.drawPolyline([QPointF(inner.left() + 1, inner.bottom() - 1),
                                  QPointF(inner.left() + inner.width() * 0.45, inner.center().y()),
                                  QPointF(inner.right() - 1, inner.bottom() - 1)])
        else:
            half_w, half_h = inner.width() / 2, inner.height() / 2
            for dx in (0, half_w + 1):
                for dy in (0, half_h + 1):
                    painter.drawRoundedRect(QRectF(inner.left() + dx, inner.top() + dy, half_w - 1, half_h - 1), 1.5, 1.5)


Scope = tuple[str, str | None, str | None]  # (kind, library folder or None for all, album or None)


class Sidebar(QFrame):
    """Emits `scope_changed(kind, root, album)`.

    `root` is None for every library folder at once; `album` is None for a whole folder or library.
    The Folders section appears only when the library has more than one folder.
    """

    scope_changed = Signal(str, object, object)  # ("person", None, person id) for a person
    rename_person_requested = Signal(int)
    merge_people_requested = Signal(int, int)       # keep, other
    hide_person_requested = Signal(int, bool)
    review_requested = Signal()
    check_person_requested = Signal(int)   # show their photos least typical face first
    new_group_requested = Signal(object)   # person ids to start with
    edit_group_requested = Signal(int)
    rename_group_requested = Signal(int)
    delete_group_requested = Signal(int)
    add_to_group_requested = Signal(int, int)  # group, person
    collapsed_changed = Signal(object)  # sorted list of collapsed section keys
    remove_folder_requested = Signal(str)
    hide_album_requested = Signal(str, str, bool)  # root, album, hide (False: show again)
    add_folder_requested = Signal()

    def __init__(self, loader: ThumbnailLoader, theme, parent=None):
        super().__init__(parent)
        self.setProperty("role", "sidebar")
        self.setMinimumWidth(170)
        self.setMaximumWidth(340)
        self.loader = loader
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 10)
        self.tree = QTreeView()
        self.tree.setObjectName("sidebar")
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setItemsExpandable(False)
        self.tree.setMouseTracking(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.tree.setItemDelegate(SidebarDelegate(loader, theme, self.tree))
        self.tree.setAccessibleName("Library and albums")
        layout.addWidget(self.tree)

        self.model = QStandardItemModel(self)
        self.collapsed: set[str] = set()  # section keys the user folded away
        self.show_all_people = False      # list unnamed people in only a few photos too
        self._people: list[Person] = []
        self._suggestions = 0
        self.library_header = self._header("Library", "library")
        self.folders_header = self._header("Folders", "folders")
        self.people_header = self._header("People", "people")
        self.unnamed_header = self._header("Unnamed People", "unnamed")
        self.groups_header = self._header("Groups", "groups")
        self._groups: list[PeopleGroup] = []
        self.albums_header = self._header("Albums", "albums")
        self.hidden_people_header = self._header("Hidden People", "hidden_people")
        # Listed only while some album is hidden; its albums still open when clicked.
        self.hidden_header = self._header("Hidden Albums", "hidden")
        self.model.appendRow(self.library_header)
        self._library_items: dict[str, QStandardItem] = {}
        for kind, label in LIBRARY_ENTRIES:
            item = self._entry(label, ("kind", kind))
            self.library_header.appendRow(item)
            self._library_items[kind] = item
        self.model.appendRow(self.albums_header)
        self.tree.setModel(self.model)
        self._apply_collapsed()
        self.tree.clicked.connect(self._clicked)
        self.tree.selectionModel().currentChanged.connect(self._current_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        loader.updated.connect(lambda _item_id: self.tree.viewport().update())
        self._scope: Scope = ("all", None, None)
        self._roots: list[str] = []
        self._names: dict[str, str] = {}
        self._quiet = False
        self.select_scope("all", None, None)

    @staticmethod
    def _header(text: str, key: str) -> QStandardItem:
        item = QStandardItem(text)
        item.setData(key, HeaderRole)
        item.setToolTip(f"Click to show or hide {text}")
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        return item

    def _headers(self) -> list[QStandardItem]:
        return [self.library_header, self.folders_header, self.people_header, self.unnamed_header, self.groups_header,
                self.albums_header, self.hidden_header, self.hidden_people_header]

    def set_collapsed(self, keys) -> None:
        """Fold these sections (for example as saved from the last session)."""
        self.collapsed = {key for key in keys if key in SIDEBAR_SECTIONS}
        self._apply_collapsed()

    def toggle_section(self, key: str) -> None:
        self.collapsed ^= {key}
        self._apply_collapsed()
        # Folding a section can take the highlight off the current entry; put it back on reopening.
        self.select_scope(*self._scope, notify=False)
        self.collapsed_changed.emit(sorted(self.collapsed))

    def _apply_collapsed(self) -> None:
        """Open every listed section except the folded ones. Runs after any rebuild of the list."""
        quiet, self._quiet = getattr(self, "_quiet", False), True  # collapsing may move the current row
        try:
            for header in self._headers():
                folded = header.data(HeaderRole) in self.collapsed
                header.setData(folded, CollapsedRole)
                if header.model() is not None:
                    self.tree.setExpanded(header.index(), not folded)
        finally:
            self._quiet = quiet

    def _clicked(self, index: QModelIndex) -> None:
        key = index.data(HeaderRole)
        if key:
            self.toggle_section(key)
        elif index.data(ReviewRole):
            self.review_requested.emit()
        elif index.data(MoreRole):
            self.show_all_people = not self.show_all_people
            self.set_people(self._people, self._suggestions)

    @staticmethod
    def _entry(text: str, scope: tuple) -> QStandardItem:
        item = QStandardItem(text)
        item.setData(scope, ScopeRole)
        item.setToolTip(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        return item

    @property
    def scope(self) -> Scope:
        return self._scope

    def folder_name(self, root: str) -> str:
        return self._names.get(root, Path(root).name)

    def set_folders(self, roots: list[str], counts: dict[str, int] | None = None) -> None:
        """Show the library folders (only listed when there is more than one)."""
        kind, root, album = self._scope
        self._roots = list(roots)
        self._names = display_names(roots)
        self._quiet = True
        try:
            listed = self.folders_header.row() >= 0 and self.folders_header.model() is not None
            if listed and len(roots) < 2:
                self.model.takeRow(self.folders_header.row())
            elif not listed and len(roots) >= 2:
                self.model.insertRow(1, self.folders_header)
            self.folders_header.removeRows(0, self.folders_header.rowCount())
            if len(roots) >= 2:
                for folder in roots:
                    item = self._entry(self._names[folder], ("folder", folder))
                    item.setToolTip(folder)
                    item.setData((counts or {}).get(folder), CountRole)
                    self.folders_header.appendRow(item)
            self._apply_collapsed()
        finally:
            self._quiet = False
        if root is not None and root not in roots:
            self.select_scope("all", None, None)  # its folder was removed from the library
        else:
            self.select_scope(kind, root, album, notify=False)

    def set_counts(self, counts: dict[str, int]) -> None:
        for kind, item in self._library_items.items():
            item.setData(counts.get(kind), CountRole)

    def set_albums(self, albums: list[Album], hidden=frozenset()) -> None:
        """Replace the album lists, keeping the current selection when that album still exists.

        Albums whose (root, name) is in `hidden` go to the Hidden Albums section.
        """
        kind, root, album = self._scope
        names = [entry.name for entry in albums]
        hidden = set(hidden)
        shown = [entry for entry in albums if (entry.root, entry.name) not in hidden]
        tucked = [entry for entry in albums if (entry.root, entry.name) in hidden]
        self._quiet = True
        try:
            for header, entries in ((self.albums_header, shown), (self.hidden_header, tucked)):
                header.removeRows(0, header.rowCount())
                for entry in entries:
                    # The same album name in two folders: say which folder each one is in.
                    label = entry.name if names.count(entry.name) < 2 else f"{entry.name} — {self.folder_name(entry.root)}"
                    item = self._entry(label, ("album", entry.root, entry.name))
                    item.setData(entry.count, CountRole)
                    item.setData(entry.cover, CoverRole)
                    header.appendRow(item)
            self.albums_header.setText("Albums" if shown else "Albums (none)")
            listed = self.hidden_header.model() is not None
            if tucked and not listed:
                self.model.appendRow(self.hidden_header)
            elif not tucked and listed:
                self.model.takeRow(self.hidden_header.row())
            self._apply_collapsed()
        finally:
            self._quiet = False
        if kind not in ("person", "group") and album is not None \
                and (root, album) not in {(entry.root, entry.name) for entry in albums}:
            self.select_scope("all", None, None)  # the album went away (renamed or emptied)
        else:
            self.select_scope(kind, root, album, notify=False)

    def set_people(self, people: list[Person], suggestions: int = 0) -> None:
        """List the people found in photos: named ones under People, the rest under Unnamed People.

        Each section only shows when it has someone. People starts with a "Review Suggestions" row
        when there are `suggestions`. Unnamed people in only a few photos sit behind "Show N More…".
        """
        kind, root, current = self._scope
        self._people, self._suggestions = list(people), suggestions
        visible = [person for person in people if not person.hidden]
        named = [person for person in visible if person.name]
        unnamed = [person for person in visible if not person.name]
        few = [person for person in unnamed if person.count < FEW_PHOTOS
               and not (kind == "person" and person.id == current)]
        self._quiet = True
        try:
            sections = (
                (self.people_header, named, [self.unnamed_header, self.groups_header, self.albums_header]),
                (self.unnamed_header, unnamed if self.show_all_people else [p for p in unnamed if p not in few],
                 [self.groups_header, self.albums_header]),
                (self.hidden_people_header, [person for person in people if person.hidden], []),
            )
            for header, entries, after_me in sections:
                header.removeRows(0, header.rowCount())
                if header is self.people_header and visible and suggestions:
                    review = QStandardItem("Review Suggestions")
                    review.setData(True, ReviewRole)
                    review.setData(suggestions, CountRole)
                    review.setToolTip("Faces that look like someone: confirm or reject them")
                    review.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    header.appendRow(review)
                for person in entries:
                    item = self._entry(person.name or UNNAMED, ("person", person.id))
                    item.setData(person.count, CountRole)
                    item.setData(person.cover, CoverRole)
                    item.setData(person.box, FaceBoxRole)
                    header.appendRow(item)
                if header is self.unnamed_header and few:
                    more = QStandardItem("Show Fewer" if self.show_all_people else f"Show {len(few):,} More…")
                    more.setData(True, MoreRole)
                    more.setToolTip(f"Unnamed people in fewer than {FEW_PHOTOS} photos")
                    more.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    header.appendRow(more)
                self._place(header, header.rowCount() > 0, after_me)
            self._apply_collapsed()
        finally:
            self._quiet = False
        if kind == "person" and current not in {person.id for person in people}:
            self.select_scope("all", None, None)  # merged away, or no photos left
        else:
            self.select_scope(kind, root, current, notify=False)

    def _place(self, header: QStandardItem, wanted: bool, before: list[QStandardItem]) -> None:
        """Show or remove a section, keeping the order: it goes before the first listed of `before`."""
        listed = header.model() is not None
        if wanted and not listed:
            following = next((section for section in before if section.model() is not None), None)
            if following is not None:
                self.model.insertRow(following.row(), header)
            else:
                self.model.appendRow(header)
        elif not wanted and listed:
            self.model.takeRow(header.row())

    def set_groups(self, groups: list[PeopleGroup]) -> None:
        """List the groups of people, after People (only while there is a group)."""
        kind, root, current = self._scope
        self._groups = list(groups)
        self._quiet = True
        try:
            header = self.groups_header
            header.removeRows(0, header.rowCount())
            for group in groups:
                item = self._entry(group.name, ("group", group.id))
                item.setData(group.count, CountRole)
                item.setData(group.cover, CoverRole)
                header.appendRow(item)
            listed = header.model() is not None
            if groups and not listed:
                self.model.insertRow(self.albums_header.row(), header)
            elif not groups and listed:
                self.model.takeRow(header.row())
            self._apply_collapsed()
        finally:
            self._quiet = False
        if kind == "group" and current not in {group.id for group in groups}:
            self.select_scope("all", None, None)  # the group was deleted
        else:
            self.select_scope(kind, root, current, notify=False)

    def group_name(self, group_id: int) -> str:
        return next((group.name for group in self._groups if group.id == group_id), "Group")

    def _person_items(self) -> list[QStandardItem]:
        """People rows (not the Review Suggestions row)."""
        return [header.child(row) for header in (self.people_header, self.unnamed_header, self.hidden_people_header)
                for row in range(header.rowCount()) if header.child(row).data(ScopeRole)]

    def person_name(self, person_id: int) -> str:
        item = next((item for item in self._person_items() if item.data(ScopeRole) == ("person", person_id)), None)
        return item.text() if item is not None else UNNAMED

    def select_album(self, name: str, root: str | None = None) -> None:
        """Select an album by name (the first one with that name, unless `root` says which)."""
        for item in self._album_items():
            scope = item.data(ScopeRole)
            if scope[2] == name and root in (None, scope[1]):
                self.select_scope("all", scope[1], name)
                return

    def _album_items(self) -> list[QStandardItem]:
        return [header.child(row) for header in (self.albums_header, self.hidden_header)
                for row in range(header.rowCount())]

    def select_scope(self, kind: str, root: str | None = None, album: str | None = None, notify: bool = True) -> None:
        target = self._find(kind, root, album)
        if target is None:
            return
        self._quiet = not notify
        try:
            self.tree.setCurrentIndex(target.index())
        finally:
            self._quiet = False
        if notify:
            self._set_scope(kind, root, album)

    def is_hidden(self, root: str, album: str) -> bool:
        return any(self.hidden_header.child(row).data(ScopeRole) == ("album", root, album)
                   for row in range(self.hidden_header.rowCount()))

    def clear_albums(self) -> None:
        self.set_albums([])
        self.set_counts({})

    def _find(self, kind: str, root: str | None, album: str | None) -> QStandardItem | None:
        if kind == "person":
            return next((item for item in self._person_items() if item.data(ScopeRole) == ("person", album)), None)
        if kind == "group":
            return next((self.groups_header.child(row) for row in range(self.groups_header.rowCount())
                         if self.groups_header.child(row).data(ScopeRole) == ("group", album)), None)
        if root is None:
            return self._library_items.get(kind) if album is None else None
        if album is None:
            items, wanted = [self.folders_header.child(row) for row in range(self.folders_header.rowCount())], \
                ("folder", root)
        else:
            items, wanted = self._album_items(), ("album", root, album)
        return next((item for item in items if item.data(ScopeRole) == wanted), None)

    def _current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        scope = current.data(ScopeRole)
        if scope is None or self._quiet:
            return
        if scope[0] == "kind":
            self._set_scope(scope[1], None, None)
        elif scope[0] in ("person", "group"):
            self._set_scope(scope[0], None, scope[1])
        elif scope[0] == "folder":
            self._set_scope("all", scope[1], None)
        else:
            self._set_scope("all", scope[1], scope[2])

    def _set_scope(self, kind: str, root: str | None, album: str | None) -> None:
        if (kind, root, album) != self._scope:
            self._scope = (kind, root, album)
            self.scope_changed.emit(kind, root, album)

    def _context_menu(self, position) -> None:
        scope = self.tree.indexAt(position).data(ScopeRole)
        menu = QMenu(self)
        if scope and scope[0] == "folder":
            menu.addAction(f"Remove “{self.folder_name(scope[1])}” from Library…",
                           lambda: self.remove_folder_requested.emit(scope[1]))
            menu.addSeparator()
        elif scope and scope[0] == "album":
            hidden = self.is_hidden(scope[1], scope[2])
            menu.addAction("Show Album in All Photos" if hidden else "Hide Album",
                           lambda: self.hide_album_requested.emit(scope[1], scope[2], not hidden))
            menu.addSeparator()
        elif scope and scope[0] == "person":
            person = scope[1]
            menu.addAction("Name…", lambda: self.rename_person_requested.emit(person))
            menu.addAction("Check Faces (Least Typical First)", lambda: self.check_person_requested.emit(person))
            others = [item for item in self._person_items() if item.data(ScopeRole) != scope]
            if others:
                merge = menu.addMenu("Merge Into")
                for other in others:
                    target = other.data(ScopeRole)[1]
                    merge.addAction(other.text(), lambda target=target: self.merge_people_requested.emit(target, person))
            hidden = self.hidden_people_header.model() is not None and any(
                item.data(ScopeRole) == scope for item in
                (self.hidden_people_header.child(row) for row in range(self.hidden_people_header.rowCount())))
            menu.addAction("Show Person" if hidden else "Hide Person",
                           lambda: self.hide_person_requested.emit(person, not hidden))
            groups = menu.addMenu("Add to Group")
            for group in self._groups:
                if person not in group.members:
                    groups.addAction(group.name, lambda group=group.id: self.add_to_group_requested.emit(group, person))
            if self._groups:
                groups.addSeparator()
            groups.addAction("New Group…", lambda: self.new_group_requested.emit([person]))
            menu.addSeparator()
        elif scope and scope[0] == "group":
            group = scope[1]
            menu.addAction("Rename…", lambda: self.rename_group_requested.emit(group))
            menu.addAction("Edit Members…", lambda: self.edit_group_requested.emit(group))
            menu.addAction("Delete Group…", lambda: self.delete_group_requested.emit(group))
            menu.addSeparator()
        menu.addAction("Add Folder to Library…", self.add_folder_requested.emit)
        menu.exec(self.tree.viewport().mapToGlobal(position))
