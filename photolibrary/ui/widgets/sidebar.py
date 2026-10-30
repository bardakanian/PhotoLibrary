"""Sidebar listing the library views (All, Photos, Videos), the library folders and their albums."""

from pathlib import Path

from PySide6.QtCore import QModelIndex, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QAbstractItemView, QFrame, QMenu, QStyle, QStyledItemDelegate, QTreeView, QVBoxLayout

from ...core.library_roots import display_names
from ...core.models import Album
from ..theme.typography import Typography
from ..thumbnails import ThumbnailLoader

ScopeRole = Qt.ItemDataRole.UserRole + 1   # ("kind", "all" | "photos" | "videos"), ("folder", root) or ("album", root, name)
CountRole = Qt.ItemDataRole.UserRole + 2
CoverRole = Qt.ItemDataRole.UserRole + 3   # MediaItem used as the album thumbnail
HeaderRole = Qt.ItemDataRole.UserRole + 4

LIBRARY_ENTRIES = (("all", "All Photos"), ("photos", "Photos"), ("videos", "Videos"))
ICON_SIZE = 22


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
        return QSize(option.rect.width(), 28 if index.data(HeaderRole) else 32)

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

        icon = QRectF(rect.left() + 14, rect.center().y() - ICON_SIZE / 2 + 1, ICON_SIZE, ICON_SIZE)
        scope = index.data(ScopeRole)
        cover = index.data(CoverRole)
        if cover is not None:
            self._paint_cover(painter, icon, cover)
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

    def _paint_cover(self, painter: QPainter, rect: QRectF, cover) -> None:
        path = QPainterPath()
        path.addRoundedRect(rect, 5, 5)
        pixmap = self.loader.pixmap(cover, 96)
        if not self.loader.has_exact(cover, 96):
            self.loader.request(cover, 96)
        painter.save()
        painter.setClipPath(path)
        if pixmap is not None:
            side = min(pixmap.width(), pixmap.height())
            source = QRectF((pixmap.width() - side) / 2, (pixmap.height() - side) / 2, side, side)
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
        if kind == "folder":
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

    scope_changed = Signal(str, object, object)
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
        self.library_header = self._header("Library")
        self.folders_header = self._header("Folders")
        self.albums_header = self._header("Albums")
        # Listed only while some album is hidden; its albums still open when clicked.
        self.hidden_header = self._header("Hidden Albums")
        self.model.appendRow(self.library_header)
        self._library_items: dict[str, QStandardItem] = {}
        for kind, label in LIBRARY_ENTRIES:
            item = self._entry(label, ("kind", kind))
            self.library_header.appendRow(item)
            self._library_items[kind] = item
        self.model.appendRow(self.albums_header)
        self.tree.setModel(self.model)
        self.tree.expandAll()
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
    def _header(text: str) -> QStandardItem:
        item = QStandardItem(text)
        item.setData(True, HeaderRole)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        return item

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
            self.tree.expandAll()
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
            self.tree.expandAll()
        finally:
            self._quiet = False
        if album is not None and (root, album) not in {(entry.root, entry.name) for entry in albums}:
            self.select_scope("all", None, None)  # the album went away (renamed or emptied)
        else:
            self.select_scope(kind, root, album, notify=False)

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
        menu.addAction("Add Folder to Library…", self.add_folder_requested.emit)
        menu.exec(self.tree.viewport().mapToGlobal(position))
