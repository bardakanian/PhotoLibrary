from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from ...core.formatting import format_date
from ...core.models import MediaItem
from ..thumbnails import BUCKETS, ThumbnailLoader
from ..viewer_images import ViewerImageLoader
from ..widgets.image_canvas import ImageCanvas
from ..widgets.video_player import VideoPlayer


class ViewerPage(QWidget):
    """Full-window viewer for one item at a time, with previous/next navigation."""

    closed = Signal()
    current_changed = Signal(object)          # MediaItem
    open_externally_requested = Signal(object)

    def __init__(self, thumbnails: ThumbnailLoader, parent=None):
        super().__init__(parent)
        self.setObjectName("viewerPage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.thumbnails = thumbnails
        self.images = ViewerImageLoader(self)
        self.images.loaded.connect(self._image_loaded)
        self.items: list[MediaItem] = []
        self.index = 0
        self._full_requested: set[int] = set()
        self._screen_decoded = False
        self.hour12 = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        top = QFrame()
        top.setProperty("role", "viewerBar")
        bar = QHBoxLayout(top)
        bar.setContentsMargins(14, 8, 14, 8)
        bar.setSpacing(8)
        back = self._button("‹  Library", "Back to the library (Esc)")
        back.clicked.connect(self.close_viewer)
        bar.addWidget(back)
        bar.addSpacing(8)
        self.previous_button = self._button("‹", "Previous (←)", compact=True)
        self.previous_button.clicked.connect(lambda: self.step(-1))
        self.next_button = self._button("›", "Next (→)", compact=True)
        self.next_button.clicked.connect(lambda: self.step(1))
        bar.addWidget(self.previous_button)
        bar.addWidget(self.next_button)
        bar.addStretch(1)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.title = QLabel()
        self.title.setStyleSheet("font-weight: 600;")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle = QLabel()
        self.subtitle.setProperty("muted", True)
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        bar.addLayout(titles)
        bar.addStretch(1)
        self.zoom_label = QLabel()
        self.zoom_label.setProperty("muted", True)
        self.zoom_label.setMinimumWidth(48)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bar.addWidget(self.zoom_label)
        self.fit_button = self._button("Fit", "Zoom to fit (⌘0)")
        self.fit_button.clicked.connect(lambda: self.canvas.toggle_actual_size())
        bar.addWidget(self.fit_button)
        self.position_label = QLabel()
        self.position_label.setProperty("muted", True)
        self.position_label.setMinimumWidth(90)
        self.position_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bar.addWidget(self.position_label)
        self.top_bar_layout = bar
        layout.addWidget(top)

        self.content = QStackedWidget()
        self.canvas = ImageCanvas()
        self.canvas.zoom_changed.connect(self._zoom_changed)
        self.canvas.needs_full_resolution.connect(self._request_full_resolution)
        self.video = VideoPlayer()
        self.video.open_externally_requested.connect(lambda: self.open_externally_requested.emit(self.current_item))
        self.content.addWidget(self.canvas)
        self.content.addWidget(self.video)
        layout.addWidget(self.content, 1)

        def shortcut(keys, slot):
            item = QShortcut(QKeySequence(keys), self)
            item.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            item.activated.connect(slot)

        shortcut("Esc", self.close_viewer)
        shortcut("Left", lambda: self.step(-1))
        shortcut("Right", lambda: self.step(1))
        shortcut("Home", lambda: self.go_to(0))
        shortcut("End", lambda: self.go_to(len(self.items) - 1))
        shortcut("Space", self._space)
        shortcut("Shift+Left", lambda: self.video.seek_by(-5000))
        shortcut("Shift+Right", lambda: self.video.seek_by(5000))
        shortcut("M", self.video.toggle_mute)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    @staticmethod
    def _button(text: str, tooltip: str, compact: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if compact:
            button.setProperty("compact", True)
        return button

    # Public API ------------------------------------------------------------

    @property
    def current_item(self) -> MediaItem | None:
        return self.items[self.index] if 0 <= self.index < len(self.items) else None

    def show_items(self, items: list[MediaItem], index: int) -> None:
        self.items = items
        self.go_to(index)
        self.setFocus()

    def go_to(self, index: int) -> None:
        if not self.items:
            return
        self.index = max(0, min(len(self.items) - 1, index))
        self._show_current()

    def step(self, direction: int) -> None:
        if 0 <= self.index + direction < len(self.items):
            self.go_to(self.index + direction)

    def close_viewer(self) -> None:
        self.stop()
        self.closed.emit()

    def stop(self) -> None:
        self.video.stop()

    def replace_items(self, items: list[MediaItem]) -> None:
        """Keep viewing the same item after the library was reloaded underneath."""
        current = self.current_item
        self.items = items
        if current is None:
            return
        for index, item in enumerate(items):
            if item.id == current.id:
                self.index = index
                self._update_labels(item)
                return
        if items:
            self.go_to(min(self.index, len(items) - 1))
        else:
            self.close_viewer()

    def set_hour12(self, hour12: bool) -> None:
        self.hour12 = hour12
        if self.current_item is not None:
            self._update_labels(self.current_item)

    def shutdown(self) -> None:
        self.video.stop()
        self.images.shutdown()

    # Showing items -----------------------------------------------------------

    def _display_edge(self) -> int:
        """Longest side worth decoding for the screen: device pixels of the screen's long side."""
        screen = self.screen()
        if screen is None:
            return 2560
        size = screen.size()
        return int(max(size.width(), size.height()) * screen.devicePixelRatio())

    def _update_labels(self, item: MediaItem) -> None:
        self.title.setText(item.name)
        self.subtitle.setText(format_date(item.captured_at, hour12=self.hour12))
        self.position_label.setText(f"{self.index + 1:,} of {len(self.items):,}")
        self.previous_button.setEnabled(self.index > 0)
        self.next_button.setEnabled(self.index < len(self.items) - 1)

    def _show_current(self) -> None:
        item = self.current_item
        if item is None:
            return
        self._update_labels(item)
        self._full_requested.clear()
        self.current_changed.emit(item)
        if item.is_video:
            self.zoom_label.clear()
            self.fit_button.hide()
            self.canvas.clear()
            self.content.setCurrentWidget(self.video)
            self.video.load(item.path)
            self.images.focus([])
        else:
            self.video.stop()
            self.fit_button.show()
            self.content.setCurrentWidget(self.canvas)
            self._show_photo(item)
        self._prefetch_neighbours()

    def _show_photo(self, item: MediaItem) -> None:
        edge = self._display_edge()
        natural = (item.width, item.height) if item.width and item.height else None
        decoded = self.images.cached(item, edge)
        self._screen_decoded = decoded is not None
        if decoded is not None:
            self.canvas.set_content(QPixmap.fromImage(decoded), natural)
        else:
            # Show the grid thumbnail at once; the sharp decode replaces it in place.
            preview = self.thumbnails.pixmap(item, BUCKETS[-1])
            self.canvas.set_content(preview, natural)
            if preview is None:
                self.canvas.set_message("Loading…")
            self.images.request(item, edge)

    def _prefetch_neighbours(self) -> None:
        edge = self._display_edge()
        keep = []
        current = self.current_item
        if current is not None and not current.is_video:
            keep.append((current, edge))
            if current.id in self._full_requested:
                keep.append((current, None))
        for offset in (1, -1):
            index = self.index + offset
            if 0 <= index < len(self.items) and not self.items[index].is_video:
                keep.append((self.items[index], edge))
        self.images.focus(keep)
        for item, max_edge in keep[1:]:
            if max_edge is not None and self.images.cached(item, max_edge) is None:
                self.images.request(item, max_edge)

    def _image_loaded(self, item_id: int, image, full_resolution: bool) -> None:
        current = self.current_item
        if current is None or current.id != item_id or current.is_video:
            return
        if image is None:
            if not full_resolution and not self.canvas.has_image:
                self.canvas.set_message("This photo can't be displayed.")
            return
        if not full_resolution:
            self._screen_decoded = True
        self.canvas.set_pixmap(QPixmap.fromImage(image))

    def _request_full_resolution(self) -> None:
        item = self.current_item
        # Wait for the screen-sized decode first; it is enough unless the user zooms in.
        if item is None or not self._screen_decoded or item.id in self._full_requested:
            return
        self._full_requested.add(item.id)
        self.images.request(item, None)

    def _zoom_changed(self, scale: float) -> None:
        self.zoom_label.setText("Fit" if self.canvas.is_fitted else f"{round(scale * 100)}%")
        self.fit_button.setText("100%" if self.canvas.is_fitted else "Fit")
        self.fit_button.setToolTip("Actual size (⌘0)" if self.canvas.is_fitted else "Zoom to fit (⌘0)")

    def _space(self) -> None:
        item = self.current_item
        if item is not None and item.is_video:
            self.video.toggle_play()
        else:
            self.close_viewer()
