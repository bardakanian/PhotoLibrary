import os
import sys

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ...core.formatting import format_date, format_dimensions, format_duration, format_size
from ...core.models import MediaItem

if sys.platform == "darwin":
    REVEAL_LABEL = "Reveal in Finder"
elif os.name == "nt":
    REVEAL_LABEL = "Show in File Explorer"
else:
    REVEAL_LABEL = "Show in Folder"


class InfoPanel(QFrame):
    """Details for the selected item(s). Everything comes from the index; nothing touches the disk."""

    reveal_requested = Signal(object)          # MediaItem
    open_requested = Signal(object)            # MediaItem
    trash_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("role", "infoPanel")
        self.setMinimumWidth(240)
        self.setMaximumWidth(420)
        self.hour12 = False
        self._items: list[MediaItem] = []
        self._summary_text: tuple[str, str] | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(12)

        heading = QLabel("Info")
        heading.setProperty("role", "section")
        layout.addWidget(heading)
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setStyleSheet("font-weight: 600; font-size: 15px;")
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.title)
        self.subtitle = QLabel()
        self.subtitle.setProperty("muted", True)
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.subtitle)

        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(10)
        self.grid.setColumnStretch(1, 1)
        self.values: dict[str, QLabel] = {}
        self.keys: dict[str, QLabel] = {}
        for row, (key, label) in enumerate((
            ("date", "Date taken"), ("dimensions", "Dimensions"), ("size", "File size"),
            ("duration", "Duration"), ("camera", "Camera"), ("album", "Album"), ("path", "Path"),
        )):
            key_label = QLabel(label.upper())
            key_label.setProperty("role", "infoKey")
            value = QLabel()
            value.setProperty("role", "infoValue")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.grid.addWidget(key_label, row * 2, 0, 1, 2)
            self.grid.addWidget(value, row * 2 + 1, 0, 1, 2)
            self.keys[key] = key_label
            self.values[key] = value
        self.values["path"].setProperty("muted", True)
        self.values["path"].setStyleSheet("font-size: 11px;")
        layout.addLayout(self.grid)

        self.reveal_button = QPushButton(REVEAL_LABEL)
        self.reveal_button.setToolTip(f"{REVEAL_LABEL} (⌘⇧R)")
        self.reveal_button.clicked.connect(lambda: self._items and self.reveal_requested.emit(self._items[0]))
        self.open_button = QPushButton("Open in Default App")
        self.open_button.setToolTip("Open in the default app (⌘⇧O)")
        self.open_button.clicked.connect(lambda: self._items and self.open_requested.emit(self._items[0]))
        self.trash_button = QPushButton("Move to Trash…")
        self.trash_button.setProperty("variant", "danger")
        self.trash_button.setToolTip("Move to Trash (⌘⌫)")
        self.trash_button.clicked.connect(self.trash_requested)
        layout.addSpacing(4)
        for button in (self.reveal_button, self.open_button, self.trash_button):
            layout.addWidget(button)
        layout.addStretch(1)
        self.show_items([])

    def set_hour12(self, hour12: bool) -> None:
        self.hour12 = hour12
        if self._summary_text is None:
            self.show_items(self._items)

    def show_summary(self, title: str, subtitle: str) -> None:
        """A Years/Months tile is selected: show its title and counts only."""
        self._items = []
        self._summary_text = (title, subtitle)
        self.title.setText(title)
        self.subtitle.setText(subtitle + "\nOpen it to see its photos and videos.")
        self._show_rows(set())
        self._set_buttons(reveal=False, trash=False)

    def show_items(self, items: list[MediaItem]) -> None:
        self._items = list(items)
        self._summary_text = None
        if not items:
            self.title.setText("No selection")
            self.subtitle.setText("Select a photo or video to see its details.")
            self._show_rows(set())
            self._set_buttons(reveal=False, trash=False)
            return
        if len(items) > 1:
            videos = sum(1 for item in items if item.is_video)
            photos = len(items) - videos
            parts = [f"{photos:,} photo{'s' if photos != 1 else ''}"] if photos else []
            if videos:
                parts.append(f"{videos:,} video{'s' if videos != 1 else ''}")
            self.title.setText(f"{len(items):,} items selected")
            self.subtitle.setText(", ".join(parts))
            dated = [item.captured_at for item in items if item.captured_at]
            if dated:
                first, last = min(dated), max(dated)
                span = format_date(first, with_time=False)
                if last.date() != first.date():
                    span += f" – {format_date(last, with_time=False)}"
                self.values["date"].setText(span)
            self.values["size"].setText(format_size(sum(item.file_size for item in items)))
            self._show_rows({"date", "size"} if dated else {"size"})
            self._set_buttons(reveal=False, trash=True)
            return

        item = items[0]
        self.title.setText(item.name)
        self.subtitle.setText("Video" if item.is_video else "Photo")
        self.values["date"].setText(format_date(item.captured_at, hour12=self.hour12))
        self.values["dimensions"].setText(format_dimensions(item.width, item.height))
        self.values["size"].setText(format_size(item.file_size))
        self.values["duration"].setText(format_duration(item.duration) or "—")
        self.values["camera"].setText(item.camera or "Unknown")
        self.values["album"].setText(item.album or "Library folder (no album)")
        self.values["path"].setText(item.file_path)
        self.values["path"].setToolTip(item.file_path)
        rows = {"date", "dimensions", "size", "camera", "album", "path"}
        if item.is_video:
            rows.add("duration")
        self._show_rows(rows)
        self._set_buttons(reveal=True, trash=True)

    def _show_rows(self, visible: set[str]) -> None:
        for key in self.values:
            self.keys[key].setVisible(key in visible)
            self.values[key].setVisible(key in visible)

    def _set_buttons(self, reveal: bool, trash: bool) -> None:
        self.reveal_button.setEnabled(reveal)
        self.open_button.setEnabled(reveal)
        self.trash_button.setEnabled(trash)
