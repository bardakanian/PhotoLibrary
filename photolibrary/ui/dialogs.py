from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout,
)

from ..core.formatting import format_size
from .time_format import system_uses_12_hour


class PreferencesDialog(QDialog):
    # Acted on at once (not on Save): the window does the work and reports in the status bar.
    clear_removed_requested = Signal()
    clear_all_requested = Signal()

    def __init__(self, current_theme: str, current_time_format: str = "system", watch_folder: bool = True,
                 parent=None, *, find_people: bool = False, faces_available: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(390)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(18)
        heading = QLabel("Appearance")
        heading.setProperty("role", "section")
        layout.addWidget(heading)
        form = QFormLayout()
        form.setSpacing(12)
        self.theme = QComboBox()
        self.theme.addItem("Follow system appearance", "system")
        self.theme.addItem("Light", "light")
        self.theme.addItem("Dark", "dark")
        self.theme.setCurrentIndex(max(0, self.theme.findData(current_theme)))
        form.addRow("Theme", self.theme)
        self.time_format = QComboBox()
        system_clock = "12-hour" if system_uses_12_hour() else "24-hour"
        self.time_format.addItem(f"Use system setting ({system_clock})", "system")
        self.time_format.addItem("12-hour (1:30 PM)", "12")
        self.time_format.addItem("24-hour (13:30)", "24")
        self.time_format.setCurrentIndex(max(0, self.time_format.findData(current_time_format)))
        self.time_format.setAccessibleName("Time format")
        form.addRow("Time format", self.time_format)
        layout.addLayout(form)
        library_heading = QLabel("Library")
        library_heading.setProperty("role", "section")
        layout.addWidget(library_heading)
        self.watch_folder = QCheckBox("Update automatically when files change in the library folder")
        self.watch_folder.setChecked(watch_folder)
        self.watch_folder.setToolTip("When off, use Rescan (⌘R) after adding, moving or deleting files.")
        layout.addWidget(self.watch_folder)
        self.find_people = QCheckBox("Find people in photos")
        self.find_people.setChecked(find_people and faces_available)
        self.find_people.setEnabled(faces_available)
        layout.addWidget(self.find_people)
        people_note = QLabel(
            "Faces are found and grouped on this Mac; no photo or face ever leaves it. The first time, "
            "two small open models (about 38 MB) are downloaded from the OpenCV model zoo."
            if faces_available else "Needs OpenCV: run pip install opencv-python-headless, then reopen the app.")
        people_note.setProperty("muted", True)
        people_note.setWordWrap(True)
        layout.addWidget(people_note)
        storage_heading = QLabel("Storage")
        storage_heading.setProperty("role", "section")
        layout.addWidget(storage_heading)
        self.cache_label = QLabel("Thumbnails: measuring…")
        self.cache_label.setWordWrap(True)
        layout.addWidget(self.cache_label)
        cache_buttons = QHBoxLayout()
        self.clear_removed_button = QPushButton("Free Space from Removed Folders")
        self.clear_removed_button.setToolTip(
            "Delete the thumbnails kept for folders you removed from the library. If you add such a "
            "folder back, its thumbnails are made again.")
        self.clear_removed_button.setEnabled(False)
        self.clear_removed_button.clicked.connect(self.clear_removed_requested)
        self.clear_all_button = QPushButton("Delete All Thumbnails…")
        self.clear_all_button.setToolTip("Delete every cached thumbnail. They are made again in the background.")
        self.clear_all_button.clicked.connect(self.clear_all_requested)
        cache_buttons.addWidget(self.clear_removed_button)
        cache_buttons.addWidget(self.clear_all_button)
        cache_buttons.addStretch(1)
        layout.addLayout(cache_buttons)
        cache_note = QLabel("Thumbnails of a folder you remove from the library are kept, so adding it "
                            "back is quick. Your photos are never touched.")
        cache_note.setProperty("muted", True)
        cache_note.setWordWrap(True)
        layout.addWidget(cache_note)
        note = QLabel("System follows your macOS appearance and clock settings.")
        note.setProperty("muted", True)
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def set_cache_info(self, files: int, size: int, removed_files: int, busy: bool = False) -> None:
        """Show the thumbnail cache's size and how many removed-folder files it still keeps."""
        text = f"Thumbnails: {format_size(size)} ({files:,} files)"
        if removed_files:
            text += f", including those of {removed_files:,} files in removed folders"
        self.cache_label.setText(text if not busy else "Thumbnails: working…")
        self.clear_removed_button.setEnabled(bool(removed_files) and not busy)
        self.clear_all_button.setEnabled(bool(files) and not busy)

    @property
    def selected_theme(self) -> str:
        return self.theme.currentData()

    @property
    def selected_find_people(self) -> bool:
        return self.find_people.isChecked()

    @property
    def selected_watch_folder(self) -> bool:
        return self.watch_folder.isChecked()

    @property
    def selected_time_format(self) -> str:
        return self.time_format.currentData()


class GroupDialog(QDialog):
    """Name a group and tick its people. A group shows the photos where all of them appear together."""

    def __init__(self, people, name: str = "", selected=(), title: str = "New Group", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumSize(360, 460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)
        self.name = QLineEdit(name)
        self.name.setPlaceholderText("Family, Friends, …")
        self.name.setAccessibleName("Group name")
        form = QFormLayout()
        form.addRow("Name", self.name)
        layout.addLayout(form)
        note = QLabel("Shows the photos in which everyone ticked appears together.")
        note.setProperty("muted", True)
        note.setWordWrap(True)
        layout.addWidget(note)
        self.list = QListWidget()
        self.list.setAccessibleName("People in the group")
        self.list.setSpacing(3)
        chosen = set(selected)
        for person in sorted(people, key=lambda entry: (not entry.name, entry.name.casefold(), -entry.count)):
            label = person.name or f"Unnamed person ({person.count:,} photos)"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, person.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if person.id in chosen else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        layout.addWidget(self.list, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.name.textChanged.connect(self._update_save)
        self.list.itemChanged.connect(self._update_save)
        self._update_save()

    @property
    def selected_name(self) -> str:
        return self.name.text().strip()

    @property
    def selected_members(self) -> list[int]:
        return [self.list.item(row).data(Qt.ItemDataRole.UserRole) for row in range(self.list.count())
                if self.list.item(row).checkState() == Qt.CheckState.Checked]

    def _update_save(self, *_args) -> None:
        # A group needs a name and at least two people (one person already has their own list).
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(
            bool(self.selected_name) and len(self.selected_members) >= 2)
