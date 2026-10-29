"""A virtualized, sectioned thumbnail grid.

`MediaGridView` is a QAbstractItemView whose geometry comes from core.grid_layout, so
painting, hit-testing and selection only ever touch the rows on screen. Qt's selection
model provides Cmd/Shift-click, keyboard navigation and select-all on top of it.
"""

from PySide6.QtCore import QEvent, QItemSelection, QItemSelectionModel, QModelIndex, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF, QRegion
from PySide6.QtWidgets import QAbstractItemView, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ..core.formatting import format_duration
from ..core.grid_layout import GridLayout
from ..core.settings import MAX_ZOOM, MIN_ZOOM
from .grid_model import GridModel, ItemRole, SummaryRole
from .theme.typography import Typography
from .thumbnails import CACHE_BYTES, ThumbnailLoader, bucket_for

# Per-mode geometry: target cell width, spacing, margin, header height, gap, aspect.
MODE_GEOMETRY = {
    "all": dict(spacing=2, margin=14, header_height=46, section_gap=6, aspect=1.0),
    "months": dict(spacing=14, margin=24, header_height=58, section_gap=12, aspect=0.72),
    "years": dict(spacing=18, margin=24, header_height=14, section_gap=0, aspect=0.58),
}
TILE_TARGET = {"months": 300, "years": 520}


def _crop_source(pixmap_width: int, pixmap_height: int, target: QRectF) -> QRectF:
    """Centre crop of the pixmap that has the target's aspect ratio (aspect fill)."""
    ratio = target.width() / max(1.0, target.height())
    if pixmap_width / max(1, pixmap_height) > ratio:
        width = pixmap_height * ratio
        return QRectF((pixmap_width - width) / 2, 0, width, pixmap_height)
    height = pixmap_width / ratio
    return QRectF(0, (pixmap_height - height) / 2, pixmap_width, height)


class ThumbnailDelegate(QStyledItemDelegate):
    def __init__(self, loader: ThumbnailLoader, theme, parent=None):
        super().__init__(parent)
        self.loader = loader
        self.theme = theme
        self.badge_font = QFont()
        self.badge_font.setPixelSize(Typography.CAPTION)
        self.badge_font.setWeight(QFont.Weight.DemiBold)
        self.title_font = QFont()
        self.title_font.setWeight(QFont.Weight.Bold)
        self.subtitle_font = QFont()
        self.subtitle_font.setPixelSize(Typography.BODY)
        self.subtitle_font.setWeight(QFont.Weight.Medium)

    def bucket_for_rect(self, rect: QRect, ratio: float, cover: bool = False) -> int:
        return bucket_for(min(rect.width(), rect.height()) * ratio, cover)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        item = index.data(ItemRole)
        if item is None:
            return
        summary = index.data(SummaryRole)
        colors = self.theme.colors
        rect = QRectF(option.rect)
        ratio = painter.device().devicePixelRatioF() if painter.device() else 1.0
        bucket = self.bucket_for_rect(option.rect, ratio, cover=summary is not None)
        pixmap = self.loader.pixmap(item, bucket)
        if not self.loader.has_exact(item, bucket):
            self.loader.request(item, bucket)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)

        painter.save()
        if summary is not None:
            path = QPainterPath()
            path.addRoundedRect(rect, 12, 12)
            painter.setClipPath(path)
        if pixmap is not None:
            painter.drawPixmap(rect, pixmap, _crop_source(pixmap.width(), pixmap.height(), rect))
        else:
            painter.fillRect(rect, QColor(colors.surface_alt))
            if self.loader.is_unavailable(item) and rect.width() >= 70:
                painter.setPen(QColor(colors.text_subtle))
                painter.setFont(self.badge_font)
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No preview")

        if summary is not None:
            self._paint_summary(painter, rect, summary)
        elif item.is_video:
            self._paint_video_badge(painter, rect, item.duration)
        elif item.is_live:
            self._paint_live_badge(painter, rect)

        if selected:
            accent = QColor(colors.accent)
            overlay = QColor(accent)
            overlay.setAlpha(55)
            painter.fillRect(rect, overlay)
            painter.setClipping(False)
            pen = QPen(accent, 3)
            pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if summary is not None:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.drawRoundedRect(rect.adjusted(1.5, 1.5, -1.5, -1.5), 12, 12)
            else:
                painter.drawRect(rect.adjusted(1.5, 1.5, -1.5, -1.5))
                self._paint_check(painter, rect, accent)
        elif option.state & QStyle.StateFlag.State_HasFocus:
            painter.setClipping(False)
            painter.setPen(QPen(QColor(colors.accent), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect.adjusted(1, 1, -1, -1))
        painter.restore()

    def _paint_live_badge(self, painter: QPainter, rect: QRectF) -> None:
        """Apple's Live Photo mark: a ring around a dot, top left."""
        if rect.width() < 48:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        centre = QPointF(rect.left() + 14, rect.top() + 14)
        for colour, width in ((QColor(0, 0, 0, 90), 3.2), (QColor(255, 255, 255, 240), 1.6)):
            pen = QPen(colour, width)
            pen.setDashPattern([1.2, 1.4])
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(centre, 8.0, 8.0)
            painter.setPen(QPen(colour, width - 0.4))
            painter.drawEllipse(centre, 5.0, 5.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 240))
        painter.drawEllipse(centre, 2.2, 2.2)

    def _paint_video_badge(self, painter: QPainter, rect: QRectF, duration: float | None) -> None:
        if rect.width() < 48:
            return
        band = min(26.0, rect.height() * 0.3)
        gradient = QLinearGradient(rect.left(), rect.bottom() - band, rect.left(), rect.bottom())
        gradient.setColorAt(0, QColor(0, 0, 0, 0))
        gradient.setColorAt(1, QColor(0, 0, 0, 150))
        painter.fillRect(QRectF(rect.left(), rect.bottom() - band, rect.width(), band), gradient)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 235))
        x, y = rect.left() + 7, rect.bottom() - 7
        painter.drawPolygon(QPolygonF([QPointF(x, y - 10), QPointF(x, y), QPointF(x + 8.5, y - 5)]))
        text = format_duration(duration)
        if text:
            painter.setFont(self.badge_font)
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(rect.adjusted(0, 0, -7, -4), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, text)

    def _paint_check(self, painter: QPainter, rect: QRectF, accent: QColor) -> None:
        if rect.width() < 40:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        size = 20.0
        circle = QRectF(rect.right() - size - 6, rect.bottom() - size - 6, size, size)
        painter.setPen(QPen(QColor(255, 255, 255), 1.5))
        painter.setBrush(accent)
        painter.drawEllipse(circle)
        pen = QPen(QColor(255, 255, 255), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        c = circle.center()
        painter.drawPolyline(QPolygonF([QPointF(c.x() - 4.5, c.y()), QPointF(c.x() - 1.2, c.y() + 3.3),
                                        QPointF(c.x() + 4.8, c.y() - 3.2)]))

    def _paint_summary(self, painter: QPainter, rect: QRectF, summary) -> None:
        shade = rect.height() * 0.55
        gradient = QLinearGradient(rect.left(), rect.bottom() - shade, rect.left(), rect.bottom())
        gradient.setColorAt(0, QColor(0, 0, 0, 0))
        gradient.setColorAt(1, QColor(0, 0, 0, 175))
        painter.fillRect(QRectF(rect.left(), rect.bottom() - shade, rect.width(), shade), gradient)
        large = rect.width() > 380
        self.title_font.setPixelSize(34 if large else 22)
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(self.title_font)
        padding = 16 if large else 12
        text_rect = rect.adjusted(padding, 0, -padding, -(padding + 20))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, summary.title)
        painter.setFont(self.subtitle_font)
        painter.setPen(QColor(255, 255, 255, 215))
        painter.drawText(rect.adjusted(padding, 0, -padding, -padding),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, summary.subtitle)


class MediaGridView(QAbstractItemView):
    open_requested = Signal(QModelIndex)   # double-click, Return or Space
    zoom_requested = Signal(int)           # new target cell size in points

    def __init__(self, loader: ThumbnailLoader, theme, parent=None):
        super().__init__(parent)
        self.loader = loader
        self.theme = theme
        self.mode = "all"
        self.zoom = 160
        self._layout = GridLayout([], 1, self.zoom)
        self._anchor_point = QPointF(0, 0)   # content coordinates of the selection anchor
        self._visible_ids: set[int] = set()
        self._pinch = 0.0
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QAbstractItemView.Shape.NoFrame)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setItemDelegate(ThumbnailDelegate(loader, theme, self))
        self.header_font = QFont()
        self.header_font.setPixelSize(Typography.SECTION + 2)
        self.header_font.setWeight(QFont.Weight.Bold)
        self.count_font = QFont()
        self.count_font.setPixelSize(Typography.BODY)

        # Throttled, not debounced: keeps re-prioritising thumbnail work while scrolling.
        self._focus_timer = QTimer(self)
        self._focus_timer.setSingleShot(True)
        self._focus_timer.setInterval(50)
        self._focus_timer.timeout.connect(self._focus_thumbnails)
        # Once scrolling pauses, load several screens around the visible ones into memory.
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(350)
        self._idle_timer.timeout.connect(self._prefetch_around)
        self.verticalScrollBar().valueChanged.connect(self._schedule_focus)
        loader.updated.connect(self._thumbnail_updated)
        self.doubleClicked.connect(self.open_requested)

    # Configuration -------------------------------------------------------

    def gridModel(self) -> GridModel:
        return self.model()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self._relayout(keep_anchor=False)
        self.viewport().update()

    def set_zoom(self, zoom: int) -> None:
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, int(zoom)))
        if zoom == self.zoom:
            return
        self.zoom = zoom
        if self.mode == "all":
            self._relayout(keep_anchor=True)
            self.viewport().update()

    @property
    def grid_layout(self) -> GridLayout:
        return self._layout

    # Layout ----------------------------------------------------------------

    def _section_counts(self) -> list[int]:
        model = self.model()
        return [section.count for section in model.sections] if isinstance(model, GridModel) else []

    def _relayout(self, keep_anchor: bool) -> None:
        anchor = self.anchor() if keep_anchor else None
        geometry = MODE_GEOMETRY[self.mode]
        target = self.zoom if self.mode == "all" else TILE_TARGET[self.mode]
        self._layout = GridLayout(self._section_counts(), self.viewport().width(), target, **geometry)
        bar = self.verticalScrollBar()
        height = self.viewport().height()
        bar.setRange(0, max(0, self._layout.total_height - height))
        bar.setPageStep(height)
        bar.setSingleStep(max(20, int(self._layout.row_height / 3)))
        if anchor is not None:
            self.restore_anchor(*anchor)
        self._schedule_focus()

    def anchor(self) -> tuple[int, int] | None:
        """(row, offset from viewport top) of the first visible cell; used to keep position."""
        offset = self.verticalOffset()
        for row in self._layout.indexes_in(offset, offset + self.viewport().height()):
            rect = self._layout.rect_for(row)
            if rect.y + rect.height > offset:
                return row, rect.y - offset
        return None

    def restore_anchor(self, row: int, delta: int) -> None:
        if 0 <= row < self._layout.item_count:
            self.verticalScrollBar().setValue(self._layout.rect_for(row).y - delta)

    def scroll_to_row(self, row: int, at_top: bool = True) -> None:
        if not 0 <= row < self._layout.item_count:
            return
        section = self._layout.sections[self._layout.section_index_for_item(row)]
        rect = self._layout.rect_for(row)
        # Show the section header too when jumping to the first row of a section.
        target = section.top if rect.y == section.cells_top else rect.y - self._layout.header_height
        self.verticalScrollBar().setValue(target if at_top else rect.y)

    def updateGeometries(self) -> None:
        self._relayout(keep_anchor=True)
        super().updateGeometries()

    def reset(self) -> None:
        super().reset()
        self._visible_ids.clear()
        self._relayout(keep_anchor=False)
        self.viewport().update()

    def dataChanged(self, top_left, bottom_right, roles=()) -> None:
        self.viewport().update()

    # QAbstractItemView geometry API ---------------------------------------

    def horizontalOffset(self) -> int:
        return 0

    def verticalOffset(self) -> int:
        return self.verticalScrollBar().value()

    def isIndexHidden(self, index: QModelIndex) -> bool:
        return False

    def visualRect(self, index: QModelIndex) -> QRect:
        if not index.isValid() or index.row() >= self._layout.item_count:
            return QRect()
        rect = self._layout.rect_for(index.row())
        return QRect(rect.x, rect.y - self.verticalOffset(), rect.width, rect.height)

    def indexAt(self, point: QPoint) -> QModelIndex:
        row = self._layout.index_at(point.x(), point.y() + self.verticalOffset())
        return self.model().index(row, 0) if row is not None and self.model() else QModelIndex()

    def scrollTo(self, index: QModelIndex, hint=QAbstractItemView.ScrollHint.EnsureVisible) -> None:
        if not index.isValid() or index.row() >= self._layout.item_count:
            return
        rect = self._layout.rect_for(index.row())
        bar = self.verticalScrollBar()
        top, height = bar.value(), self.viewport().height()
        sticky = self._layout.header_height
        if hint == QAbstractItemView.ScrollHint.PositionAtTop:
            bar.setValue(rect.y - sticky)
        elif hint == QAbstractItemView.ScrollHint.PositionAtCenter:
            bar.setValue(rect.y - (height - rect.height) // 2)
        elif hint == QAbstractItemView.ScrollHint.PositionAtBottom:
            bar.setValue(rect.y + rect.height - height)
        elif rect.y < top + sticky:
            bar.setValue(rect.y - sticky)
        elif rect.y + rect.height > top + height:
            bar.setValue(rect.y + rect.height - height + self._layout.spacing)

    def moveCursor(self, action, modifiers) -> QModelIndex:
        model = self.model()
        count = model.rowCount() if model else 0
        if not count:
            return QModelIndex()
        current = self.currentIndex()
        if not current.isValid():
            return model.index(0, 0)
        row = current.row()
        Action = QAbstractItemView.CursorAction
        if action in (Action.MoveLeft, Action.MovePrevious):
            row = max(0, row - 1)
        elif action in (Action.MoveRight, Action.MoveNext):
            row = min(count - 1, row + 1)
        elif action == Action.MoveUp:
            row = self._layout.index_above(row)
        elif action == Action.MoveDown:
            row = self._layout.index_below(row)
        elif action == Action.MoveHome:
            row = 0
        elif action == Action.MoveEnd:
            row = count - 1
        elif action in (Action.MovePageDown, Action.MovePageUp):
            step = self._layout.index_below if action == Action.MovePageDown else self._layout.index_above
            start_y = self._layout.rect_for(row).y
            page = self.viewport().height() - self._layout.row_height
            while True:
                following = step(row)
                if following == row or abs(self._layout.rect_for(following).y - start_y) > page:
                    break
                row = following
        return model.index(row, 0)

    def setSelection(self, rect: QRect, command) -> None:
        """Select the reading-order range between the anchor and the other corner, like Photos."""
        model = self.model()
        offset = self.verticalOffset()
        anchor = QPointF(self._anchor_point.x(), self._anchor_point.y() - offset)
        normalized = rect.normalized()
        # The anchor is one corner of the rectangle; the pointer is the opposite one.
        other_x = normalized.left() if abs(anchor.x() - normalized.right()) < abs(anchor.x() - normalized.left()) else normalized.right()
        other_y = normalized.top() if abs(anchor.y() - normalized.bottom()) < abs(anchor.y() - normalized.top()) else normalized.bottom()
        if normalized.width() <= 1 and normalized.height() <= 1:
            start = end = self._layout.index_at(normalized.x(), normalized.y() + offset)
        else:
            start = self._layout.nearest_index(anchor.x(), anchor.y() + offset)
            end = self._layout.nearest_index(other_x, other_y + offset)
        selection = QItemSelection()
        if start is not None and end is not None and model is not None:
            first, last = sorted((start, end))
            selection.select(model.index(first, 0), model.index(last, 0))
        self.selectionModel().select(selection, command)

    def visualRegionForSelection(self, selection: QItemSelection) -> QRegion:
        region = QRegion()
        offset = self.verticalOffset()
        visible = self._layout.indexes_in(offset, offset + self.viewport().height())
        if not visible:
            return region
        for selection_range in selection:
            first = max(selection_range.top(), visible.start)
            last = min(selection_range.bottom(), visible.stop - 1)
            for row in range(first, last + 1):
                region += self.visualRect(self.model().index(row, 0))
        return region

    # Input ---------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self._anchor_point = QPointF(event.position().x(), event.position().y() + self.verticalOffset())
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape and self.selectionModel() and self.selectionModel().hasSelection():
            self.clearSelection()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and self.currentIndex().isValid() \
                and not event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            self.open_requested.emit(self.currentIndex())
            return
        super().keyPressEvent(event)
        if not event.modifiers() & Qt.KeyboardModifier.ShiftModifier and self.currentIndex().isValid():
            rect = self._layout.rect_for(self.currentIndex().row())
            self._anchor_point = QPointF(rect.x + rect.width / 2, rect.y + rect.height / 2)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier and self.mode == "all":
            steps = event.angleDelta().y() / 120
            if steps:
                self.zoom_requested.emit(int(self.zoom * (1.12 ** steps)))
            event.accept()
            return
        super().wheelEvent(event)

    def event(self, event) -> bool:
        # Trackpad pinch on macOS arrives as a native zoom gesture.
        if event.type() == QEvent.Type.NativeGesture and self.mode == "all":
            if event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                self.zoom_requested.emit(int(self.zoom * (1 + event.value())))
                return True
        return super().event(event)

    # Painting --------------------------------------------------------------

    def paintEvent(self, event) -> None:
        colors = self.theme.colors
        painter = QPainter(self.viewport())
        painter.fillRect(event.rect(), QColor(colors.background))
        model = self.model()
        if model is None or not self._layout.item_count:
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        offset = self.verticalOffset()
        top, bottom = offset + event.rect().top(), offset + event.rect().bottom()
        option = QStyleOptionViewItem()
        self.initViewItemOption(option)
        selection = self.selectionModel()
        current = self.currentIndex()
        delegate = self.itemDelegate()
        visible_ids = set()
        has_focus = self.hasFocus()
        for row in self._layout.indexes_in(top, bottom):
            index = model.index(row, 0)
            rect = self._layout.rect_for(row)
            option.rect = QRect(rect.x, rect.y - offset, rect.width, rect.height)
            state = QStyle.StateFlag.State_Enabled
            if selection.isSelected(index):
                state |= QStyle.StateFlag.State_Selected
            if has_focus and index == current:
                state |= QStyle.StateFlag.State_HasFocus
            option.state = state
            delegate.paint(painter, option, index)
            item = model.item_at(row)
            if item is not None:
                visible_ids.add(item.id)
        self._visible_ids = visible_ids
        self._paint_headers(painter, model, offset, top, bottom)

    def _paint_headers(self, painter: QPainter, model: GridModel, offset: int, top: int, bottom: int) -> None:
        layout = self._layout
        if layout.header_height < 30:
            return
        colors = self.theme.colors
        sections = model.sections
        for section_index in layout.sections_in(top - layout.header_height, bottom):
            geometry = layout.sections[section_index]
            if geometry.top + layout.header_height < offset:
                continue  # scrolled under the sticky header
            self._paint_header(painter, sections[section_index], geometry.count,
                               geometry.top - offset, solid=False)
        # Sticky header for the section at the top of the viewport.
        current = layout.section_index_at_y(offset + 1)
        geometry = layout.sections[current]
        if geometry.top < offset:
            y = 0
            if current + 1 < len(layout.sections):
                y = min(0, layout.sections[current + 1].top - offset - layout.header_height)
            self._paint_header(painter, sections[current], geometry.count, y, solid=True)

    def _paint_header(self, painter: QPainter, section, count: int, y: int, solid: bool) -> None:
        colors = self.theme.colors
        height = self._layout.header_height
        rect = QRect(0, y, self.viewport().width(), height)
        if solid:
            background = QColor(colors.background)
            background.setAlpha(238)
            painter.fillRect(rect, background)
        margin = self._layout.margin
        painter.setFont(self.header_font)
        painter.setPen(QColor(colors.text))
        text_rect = rect.adjusted(margin + 2, 0, -margin, -4)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, section.title)
        title_width = painter.fontMetrics().horizontalAdvance(section.title)
        painter.setFont(self.count_font)
        painter.setPen(QColor(colors.text_muted))
        noun = "months" if self.mode == "months" else "items"
        label = f"{count:,} {noun if count != 1 else noun[:-1]}"
        painter.drawText(text_rect.adjusted(title_width + 10, 2, 0, 0),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)

    # Thumbnail scheduling --------------------------------------------------

    def _schedule_focus(self, *_args) -> None:
        if not self._focus_timer.isActive():
            self._focus_timer.start()
        self._idle_timer.start()  # restarts on every scroll step, so it fires once scrolling stops

    def _focus_thumbnails(self) -> None:
        model = self.model()
        if model is None or not self._layout.item_count:
            return
        offset, height = self.verticalOffset(), self.viewport().height()
        visible = self._layout.indexes_in(offset, offset + height)
        # Visible cells first, then a screen below and half a screen above.
        ahead = self._layout.indexes_in(offset + height, offset + 2 * height)
        behind = self._layout.indexes_in(offset - height // 2, offset)
        rows = list(visible) + [row for row in ahead if row not in visible] + \
            [row for row in reversed(behind) if row not in visible]
        items = [item for item in (model.item_at(row) for row in rows) if item is not None]
        rect = self._layout.rect_for(visible.start if visible else 0)
        bucket = bucket_for(min(rect.width, rect.height) * self.devicePixelRatioF(), cover=self.mode != "all")
        self.loader.focus(items, bucket)

    def _prefetch_around(self) -> None:
        """Visible cells first, then up to 5 screens ahead and 2 behind, within the memory budget."""
        model = self.model()
        if model is None or not self._layout.item_count:
            return
        offset, height = self.verticalOffset(), self.viewport().height()
        visible = self._layout.indexes_in(offset, offset + height)
        rect = self._layout.rect_for(visible.start if visible else 0)
        bucket = bucket_for(min(rect.width, rect.height) * self.devicePixelRatioF(), cover=self.mode != "all")
        # A decoded thumbnail is about bucket x (4/3 bucket) x 4 bytes; keep a quarter of the cache spare.
        budget = max(len(visible), int(CACHE_BYTES * 0.75 // (bucket * bucket * 4 * 4 // 3)))
        rows = list(visible)
        for screen in range(1, 6):
            rows += list(self._layout.indexes_in(offset + screen * height, offset + (screen + 1) * height))
            if screen <= 2:
                behind = self._layout.indexes_in(offset - screen * height, offset - (screen - 1) * height)
                rows += [row for row in reversed(behind)]
            if len(rows) >= budget:
                break
        seen = set()
        ordered = [row for row in rows if not (row in seen or seen.add(row))][:budget]
        items = [item for item in (model.item_at(row) for row in ordered) if item is not None]
        self.loader.focus(items, bucket)

    def _thumbnail_updated(self, item_id: int) -> None:
        if item_id in self._visible_ids:
            self.viewport().update()
