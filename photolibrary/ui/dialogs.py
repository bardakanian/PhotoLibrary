from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout

from .time_format import system_uses_12_hour


class PreferencesDialog(QDialog):
    def __init__(self, current_theme: str, current_time_format: str = "system", watch_folder: bool = True,
                 parent=None):
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
        note = QLabel("System follows your macOS appearance and clock settings.")
        note.setProperty("muted", True)
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def selected_theme(self) -> str:
        return self.theme.currentData()

    @property
    def selected_watch_folder(self) -> bool:
        return self.watch_folder.isChecked()

    @property
    def selected_time_format(self) -> str:
        return self.time_format.currentData()
