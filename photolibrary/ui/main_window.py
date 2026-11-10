import dataclasses
import logging
import threading
from pathlib import Path

from PySide6.QtCore import QByteArray, QFile, QObject, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QKeySequence
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QApplication, QInputDialog, QMenu, QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QSizePolicy, QSlider, QSplitter, QStackedWidget, QStyle, QVBoxLayout, QWidget,
)

from ..core.database import MediaRepository
from ..core.grouping import LibrarySnapshot, build_snapshot
from ..core.indexer import index_folders
from ..core.library_roots import root_conflict
from ..core.media import FFMPEG_ENABLED, HEIC_ENABLED, open_in_default_app, reveal_in_file_manager
from ..core import media as media_module
from ..core.cache_cleanup import prune_thumbnail_cache
from ..core import database as database_module
from ..core.faces import FACES_ENABLED, ModelError, OpenCVFaceEngine, ensure_models, models_present
from ..core.people import run_people_pass
from ..core.constants import APP_NAME, COVER_EDGE, VERSION
from ..core.thumbnail_warmer import WarmResult, warm_thumbnails
from ..core.trash import TrashResult, trash_items
from ..core.watcher import LibraryWatcher
from ..core.formatting import format_size
from ..core.search import SearchFilter, parse_query
from ..core.settings import MAX_ZOOM, MIN_ZOOM, SettingsStore
from .dialogs import GroupDialog, PreferencesDialog
from .grid_model import GridModel
from .grid_view import MediaGridView
from .pages.grid_page import GridPage
from .pages.insights_page import LibraryInsightsPage
from .pages.review_page import ReviewPage
from .pages.viewer_page import ViewerPage
from .tasks import cancel_queued, run_async
from .theme import ThemeManager
from .thumbnails import ThumbnailLoader
from .time_format import uses_12_hour
from .widgets import SearchBox
from .viewer_images import decode_for_faces
from .widgets.common import sidebar_icon
from .widgets.info_panel import REVEAL_LABEL, InfoPanel
from .widgets.sidebar import UNNAMED, Sidebar

LOGGER = logging.getLogger(__name__)
MODE_LABELS = (("years", "Years"), ("months", "Months"), ("all", "All Photos"))


class IndexEvents(QObject):
    """Carries indexer events from its thread to the UI thread."""

    received = Signal(object)


class MainWindow(QMainWindow):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.settings_store = SettingsStore()
        self.settings = self.settings_store.load()
        # Resolved once here, so later comparisons need no disk access on the UI thread.
        self.settings.library_folders = [folder.resolve() for folder in self.settings.library_folders]
        self.repository = MediaRepository()
        self.theme = ThemeManager(app, self.settings.theme)

        self.query_pool = QThreadPool(self)
        self.query_pool.setMaxThreadCount(1)
        self.io_pool = QThreadPool(self)
        self.io_pool.setMaxThreadCount(2)
        self.query_request_id = 0
        self.applied_request_id = 0  # the newest query whose result the grid shows (or that failed)
        self.snapshot: LibrarySnapshot | None = None
        self._viewer_return: tuple[int | None, int] = (None, 0)  # (opened item id, grid scroll value)

        self.index_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.active_scan_id = 0
        self.pending_index_start = False
        self.scanning = False
        self.index_events = IndexEvents()
        self.index_events.received.connect(self._handle_index_event)
        # Background creation of every missing thumbnail, started after each scan.
        self.warm_events = IndexEvents()
        self.warm_events.received.connect(self._handle_warm_event)
        self.warm_stop = threading.Event()
        # Warming waits while a video plays: its checks are pure Python and make playback stutter.
        self.warm_pending = False   # a run was requested and hasn't finished
        self.video_playing = False
        # Folder watching: rescans automatically after files change on disk.
        self.watchers: list[LibraryWatcher] = []  # one per library folder
        self.pending_rescan = False
        self.watch_events = IndexEvents()
        self.watch_events.received.connect(self._folder_changed)
        self.watch_retry = QTimer(self)
        self.watch_retry.setSingleShot(True)
        self.watch_retry.setInterval(30_000)
        self.watch_retry.timeout.connect(self.start_watching)
        self.warm_id = 0
        # People: finding faces runs after warming, and pauses for scans and video like warming.
        self.people_events = IndexEvents()
        self.people_events.received.connect(self._handle_people_event)
        self.people_stop = threading.Event()
        self.people_pending = False
        self.people_id = 0
        self.face_engine_factory = OpenCVFaceEngine  # called with the models folder; tests use a fake
        self.ready_text = "Ready"
        self.scan_notes: list[str] = []  # "3 new or changed" and so on, from the last scan
        self.library_totals: tuple[int, int] | None = None  # (shown, hidden) items, from the sidebar query
        # While a scan is adding items, refresh the grid at most every few seconds.
        self.reload_timer = QTimer(self)
        self.reload_timer.setSingleShot(True)
        self.reload_timer.timeout.connect(self.refresh_library)
        # What the grid shows: a library kind, one library folder or an album, narrowed by the search box.
        self.scope_kind = "all"
        self.scope_root: str | None = None  # None: every library folder
        self.scope_album: str | None = None
        self.search_filter: SearchFilter | None = None
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(250)
        self.search_timer.timeout.connect(self._apply_search)
        # Coalesce selection changes (a Shift-click range is one update, not thousands).
        self.info_timer = QTimer(self)
        self.info_timer.setSingleShot(True)
        self.info_timer.setInterval(0)
        self.info_timer.timeout.connect(self._update_info)
        self.trash_in_progress = False
        self.insights_request_id = 0
        self.settings_timer = QTimer(self)
        self.settings_timer.setSingleShot(True)
        self.settings_timer.setInterval(600)
        self.settings_timer.timeout.connect(self._save_settings)

        self.loader = ThumbnailLoader(self)
        self.model = GridModel(self)
        self.model.mode = self.settings.view_mode
        self.view = MediaGridView(self.loader, self.theme)
        self.view.mode = self.settings.view_mode
        self.view.zoom = self.settings.zoom
        self.view.setModel(self.model)
        self.theme.changed.connect(lambda _preference: self.view.viewport().update())

        self.setWindowTitle(f"{APP_NAME} {VERSION}")
        self.setMinimumSize(900, 600)
        self.resize(1320, 860)
        if self.settings.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(self.settings.window_geometry.encode()))
        self._build_ui()
        self._build_menus()
        self._apply_mode_controls(self.settings.view_mode)
        self._apply_sort_controls()
        QTimer.singleShot(0, self._startup)

    @property
    def roots(self) -> list[Path]:
        """The library folders, in the order they were added."""
        return list(self.settings.library_folders)

    @property
    def root_strings(self) -> list[str]:
        return [str(folder) for folder in self.settings.library_folders]

    @property
    def hidden_albums(self) -> list[tuple[str, str]]:
        return list(self.settings.hidden_albums)

    def scope_folders(self) -> list[Path]:
        """The folders the grid currently shows: one when a folder or album is selected, else all."""
        return [Path(self.scope_root)] if self.scope_root else self.roots

    # UI construction -------------------------------------------------------

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.setCentralWidget(root)

        toolbar = self.toolbar = QFrame()
        toolbar.setProperty("role", "toolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(16, 10, 16, 10)
        toolbar_layout.setSpacing(8)
        self.sidebar_button = QPushButton()
        self.sidebar_button.setCheckable(True)
        self.sidebar_button.setChecked(self.settings.sidebar_visible)
        self.sidebar_button.setProperty("compact", True)
        self.sidebar_button.setProperty("variant", "ghost")
        self.sidebar_button.setToolTip("Hide the sidebar (⌥⌘S)" if self.settings.sidebar_visible
                                       else "Show the sidebar (⌥⌘S)")
        self.sidebar_button.setAccessibleName("Show sidebar")
        self.sidebar_button.clicked.connect(self.toggle_sidebar)
        toolbar_layout.addWidget(self.sidebar_button)
        self.folder_label = "Choose Folder"
        self.folder_button = QPushButton(self.folder_label)
        self.folder_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.folder_button.setToolTip("Add a folder of photos and videos to the library (⌘O)")
        self.folder_button.clicked.connect(self.add_folder)
        toolbar_layout.addWidget(self.folder_button)
        self.rescan_button = QPushButton("Rescan")
        self.rescan_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.rescan_button.setToolTip("Look for added, changed and removed files (⌘R)")
        self.rescan_button.clicked.connect(self.start_indexing)
        toolbar_layout.addWidget(self.rescan_button)
        self.insights_button = QPushButton("Insights")
        self.insights_button.setToolTip("Library Insights: totals, storage, busiest times (⌘⇧I)")
        self.insights_button.clicked.connect(self.show_insights)
        toolbar_layout.addWidget(self.insights_button)
        toolbar_layout.addStretch(1)

        segmented = QFrame()
        segmented.setProperty("role", "segmented")
        segmented.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        segmented_layout = QHBoxLayout(segmented)
        segmented_layout.setContentsMargins(2, 2, 2, 2)
        segmented_layout.setSpacing(2)
        self.mode_buttons: dict[str, QPushButton] = {}
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        for number, (mode, label) in enumerate(MODE_LABELS, start=1):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty("segment", True)
            # Never squeeze the labels; the search box and zoom slider give up width instead.
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            button.setToolTip(f"{label} (⌘{number})")
            button.clicked.connect(lambda _checked=False, mode=mode: self.set_view_mode(mode))
            self.mode_group.addButton(button)
            self.mode_buttons[mode] = button
            segmented_layout.addWidget(button)
        toolbar_layout.addWidget(segmented)
        self.sort_button = QPushButton()
        self.sort_button.setProperty("variant", "ghost")
        self.sort_button.setAccessibleName("Sort order")
        self.sort_button.clicked.connect(self.toggle_sort_order)
        toolbar_layout.addWidget(self.sort_button)
        toolbar_layout.addStretch(1)

        self.search_box = SearchBox("Search name or date")
        self.search_box.setAccessibleName("Search by filename or date")
        self.search_box.setToolTip("Filename words, or dates like 2023, May 2023, 2023-05-14 (⌘F)")
        self.search_box.setMinimumWidth(140)
        self.search_box.setMaximumWidth(240)
        self.search_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.search_box.textChanged.connect(lambda _text: self.search_timer.start())
        self.search_box.returnPressed.connect(self._search_return)
        self.search_box.installEventFilter(self)
        toolbar_layout.addWidget(self.search_box, 2)  # grows before the spacers around the view buttons
        toolbar_layout.addSpacing(8)

        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(MIN_ZOOM, MAX_ZOOM)
        self.zoom_slider.setValue(self.settings.zoom)
        self.zoom_slider.setMinimumWidth(70)
        self.zoom_slider.setMaximumWidth(130)
        self.zoom_slider.setAccessibleName("Thumbnail size")
        self.zoom_slider.setToolTip("Thumbnail size (⌘+ / ⌘−)")
        self.zoom_slider.valueChanged.connect(self._zoom_changed)
        self.view.zoom_requested.connect(self.zoom_slider.setValue)
        small = QLabel("▫")
        small.setProperty("muted", True)
        large = QLabel("◻")
        large.setProperty("muted", True)
        toolbar_layout.addWidget(small)
        toolbar_layout.addWidget(self.zoom_slider)
        toolbar_layout.addWidget(large)
        self.zoom_marks = (small, large)  # dropped with the button labels when space is short
        toolbar_layout.addSpacing(6)
        self.info_button = QPushButton("ⓘ")
        self.info_button.setCheckable(True)
        self.info_button.setChecked(self.settings.info_panel_visible)
        self.info_button.setProperty("compact", True)
        self.info_button.setProperty("variant", "ghost")
        self.info_button.setToolTip("Show info (⌘I)")
        self.info_button.setAccessibleName("Show info")
        self.info_button.setStyleSheet("font-size: 17px;")
        self.info_button.clicked.connect(self.toggle_info)
        toolbar_layout.addWidget(self.info_button)
        self.settings_button = QPushButton("⚙")
        self.settings_button.setProperty("compact", True)
        self.settings_button.setProperty("variant", "ghost")
        self.settings_button.setToolTip("Settings (⌘,)")
        self.settings_button.setAccessibleName("Settings")
        self.settings_button.setStyleSheet("font-size: 17px;")
        self.settings_button.clicked.connect(self.show_preferences)
        toolbar_layout.addWidget(self.settings_button)
        root_layout.addWidget(toolbar)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.hide()
        root_layout.addWidget(self.progress)

        self.grid_page = GridPage(self.view)
        self.grid_page.empty_action_requested.connect(self._empty_action)
        self.view.open_requested.connect(self._open_index)
        self.viewer = ViewerPage(self.loader)
        self.viewer.set_hour12(uses_12_hour(self.settings.time_format))
        self.viewer.closed.connect(self.close_viewer)
        self.viewer.open_externally_requested.connect(self.open_in_default_app)
        self.viewer.faces_needed.connect(self._load_faces)
        self.viewer.face_menu_requested.connect(self._face_menu)
        self.viewer.set_faces_available(self.settings.find_people)
        self.viewer.video.player.playbackStateChanged.connect(self._playback_state_changed)
        self.viewer.current_changed.connect(lambda item: self.info_panel.show_items([item]))
        self.viewer_info_button = QPushButton("ⓘ")
        self.viewer_info_button.setCheckable(True)
        self.viewer_info_button.setChecked(self.settings.info_panel_visible)
        self.viewer_info_button.setProperty("compact", True)
        self.viewer_info_button.setToolTip("Show info (⌘I)")
        self.viewer_info_button.setAccessibleName("Show info")
        self.viewer_info_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.viewer_info_button.clicked.connect(self.toggle_info)
        self.viewer.top_bar_layout.addWidget(self.viewer_info_button)
        self.insights = LibraryInsightsPage(self.theme.colors)
        self.insights.back_requested.connect(self.close_insights)
        self.review = ReviewPage(self.loader, self.theme)
        self.review.back_requested.connect(self.close_review)
        self.review.answered.connect(self._review_answered)
        self.people_list: list = []  # the latest people shown in the sidebar (their covers, for Review)
        self.review_request_id = 0
        self.select_group_after_reload: int | None = None  # a group just created, shown once listed
        self.groups_list: list = []  # the latest groups shown in the sidebar
        self.least_typical = False  # a person's photos, least typical face first (for checking)
        self.insights.choose_folder_requested.connect(self.add_folder)
        self.insights.rescan_requested.connect(self.start_indexing)
        self.insights.retry_requested.connect(self.load_insights)
        self.theme.changed.connect(lambda _preference: self.insights.set_theme_colors(self.theme.colors))
        self.stack = QStackedWidget()
        self.stack.addWidget(self.grid_page)
        self.stack.addWidget(self.viewer)
        self.stack.addWidget(self.insights)
        self.stack.addWidget(self.review)
        self.sidebar = Sidebar(self.loader, self.theme)
        self.sidebar.scope_changed.connect(self.set_scope)
        self.sidebar.add_folder_requested.connect(self.add_folder)
        self.sidebar.hide_album_requested.connect(self.set_album_hidden)
        self.sidebar.remove_folder_requested.connect(lambda root: self.remove_folder(Path(root)))
        self.sidebar.rename_person_requested.connect(self.rename_person)
        self.sidebar.merge_people_requested.connect(self.merge_people)
        self.sidebar.hide_person_requested.connect(self.set_person_hidden)
        self.sidebar.review_requested.connect(self.show_review)
        self.sidebar.check_person_requested.connect(self.check_person)
        self.sidebar.new_group_requested.connect(self.new_group)
        self.sidebar.edit_group_requested.connect(self.edit_group)
        self.sidebar.rename_group_requested.connect(self.rename_group)
        self.sidebar.delete_group_requested.connect(self.delete_group)
        self.sidebar.add_to_group_requested.connect(
            lambda group, person: self._change_people(lambda repository: repository.add_to_group(group, person)))
        self.sidebar.setVisible(self.settings.sidebar_visible)
        self.sidebar.set_collapsed(self.settings.collapsed_sections)
        self.sidebar.collapsed_changed.connect(self._sidebar_sections_changed)
        self.theme.changed.connect(lambda _preference: self.sidebar.tree.viewport().update())
        self.theme.changed.connect(lambda _preference: self._update_sidebar_icon())
        self._update_sidebar_icon()
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(1)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(self.stack)
        self.info_panel = InfoPanel()
        self.info_panel.set_hour12(uses_12_hour(self.settings.time_format))
        self.info_panel.reveal_requested.connect(self.reveal_item)
        self.info_panel.open_requested.connect(self.open_in_default_app)
        self.info_panel.trash_requested.connect(self.move_to_trash)
        self.info_panel.setVisible(self.settings.info_panel_visible)
        self.splitter.addWidget(self.info_panel)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([220, 1100, 280])
        self.view.selectionModel().selectionChanged.connect(lambda *_: self.info_timer.start())
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._grid_context_menu)
        root_layout.addWidget(self.splitter, 1)

        self.status = QLabel("Ready")
        self.status.setProperty("muted", True)
        self.statusBar().addWidget(self.status, 1)
        self.folder_status = QLabel("No folder selected")
        self.folder_status.setProperty("subtle", True)
        self.folder_status.setMaximumWidth(440)
        self.statusBar().addPermanentWidget(self.folder_status)

    def _build_menus(self) -> None:
        def action(menu, text, shortcut, slot, *, checkable=False) -> QAction:
            item = QAction(text, self)
            if shortcut:
                item.setShortcut(QKeySequence(shortcut))
            item.setCheckable(checkable)
            item.triggered.connect(slot)
            menu.addAction(item)
            return item

        file_menu = self.menuBar().addMenu("File")
        action(file_menu, "Add Folder to Library…", "Ctrl+O", self.add_folder)
        self.remove_folder_menu = file_menu.addMenu("Remove Folder from Library")
        self.remove_folder_menu.aboutToShow.connect(self._fill_remove_folder_menu)
        action(file_menu, "Rescan Library", "Ctrl+R", self.start_indexing)
        file_menu.addSeparator()
        # On macOS Qt moves this into the application menu (where Settings lives on a Mac);
        # elsewhere it stays in File.
        settings = action(file_menu, "Settings…", QKeySequence.StandardKey.Preferences, self.show_preferences)
        settings.setMenuRole(QAction.MenuRole.PreferencesRole)
        if QKeySequence(QKeySequence.StandardKey.Preferences).isEmpty():
            settings.setShortcut(QKeySequence("Ctrl+,"))
        file_menu.addSeparator()
        action(file_menu, "Close Window", QKeySequence.StandardKey.Close, self.close)

        edit_menu = self.menuBar().addMenu("Edit")
        action(edit_menu, "Select All", QKeySequence.StandardKey.SelectAll, self.select_all)
        action(edit_menu, "Deselect All", "Ctrl+Shift+A", self.view.clearSelection)
        edit_menu.addSeparator()
        trash = action(edit_menu, "Move to Trash…", "Ctrl+Backspace", self.move_to_trash)
        trash.setShortcuts([QKeySequence("Ctrl+Backspace"), QKeySequence(QKeySequence.StandardKey.Delete)])
        self.remove_from_person_action = action(edit_menu, "Remove from This Person", "Alt+Backspace",
                                                self.remove_from_person)
        self.remove_from_person_action.setEnabled(False)
        edit_menu.addSeparator()
        action(edit_menu, "Find…", QKeySequence.StandardKey.Find, self.focus_search)
        action(edit_menu, "Clear Search", "", self.clear_search)

        view_menu = self.menuBar().addMenu("View")
        self.sidebar_action = action(view_menu, "Show Sidebar", "Ctrl+Alt+S", self.toggle_sidebar, checkable=True)
        self.sidebar_action.setChecked(self.settings.sidebar_visible)
        self.info_action = action(view_menu, "Show Info", "Ctrl+I", self.toggle_info, checkable=True)
        action(view_menu, "Library Insights", "Ctrl+Shift+I", self.show_insights)
        self.review_action = action(view_menu, "Review People…", "Ctrl+Shift+P", self.show_review)
        self.least_typical_action = action(view_menu, "Least Typical Faces First", "",
                                           lambda checked: self.set_least_typical(checked), checkable=True)
        self.least_typical_action.setEnabled(False)
        self.new_group_action = action(view_menu, "New Group…", "", lambda: self.new_group([]))
        self.new_group_action.setEnabled(self.settings.find_people)
        self.review_action.setEnabled(self.settings.find_people)
        self.info_action.setChecked(self.settings.info_panel_visible)
        view_menu.addSeparator()
        self.mode_actions = QActionGroup(self)
        self.mode_actions.setExclusive(True)
        self.mode_action_by_key: dict[str, QAction] = {}
        for number, (mode, label) in enumerate(MODE_LABELS, start=1):
            item = action(view_menu, label, f"Ctrl+{number}",
                          lambda _checked=False, mode=mode: self.set_view_mode(mode), checkable=True)
            self.mode_actions.addAction(item)
            self.mode_action_by_key[mode] = item
        view_menu.addSeparator()
        self.sort_actions = QActionGroup(self)
        self.sort_actions.setExclusive(True)
        self.sort_action_by_key: dict[str, QAction] = {}
        for order, label in (("newest", "Newest First"), ("oldest", "Oldest First")):
            item = action(view_menu, label, "", lambda _checked=False, order=order: self.set_sort_order(order),
                          checkable=True)
            self.sort_actions.addAction(item)
            self.sort_action_by_key[order] = item
        view_menu.addSeparator()
        action(view_menu, "Zoom In", QKeySequence.StandardKey.ZoomIn, lambda: self.step_zoom(1))
        # ZoomIn is Ctrl++ on macOS; also accept Ctrl+= so no Shift is needed.
        action(view_menu, "Zoom In ", "Ctrl+=", lambda: self.step_zoom(1)).setVisible(False)
        action(view_menu, "Zoom Out", QKeySequence.StandardKey.ZoomOut, lambda: self.step_zoom(-1))
        action(view_menu, "Actual Size / Fit", "Ctrl+0", self.toggle_actual_size)

        go_menu = self.menuBar().addMenu("Go")
        action(go_menu, "Open Viewer", "Ctrl+Down", self.open_current)
        action(go_menu, "Back to Library", "Ctrl+Up", self.close_viewer)
        go_menu.addSeparator()
        action(go_menu, REVEAL_LABEL, "Ctrl+Shift+R", lambda: self._with_target(self.reveal_item))
        action(go_menu, "Open in Default App", "Ctrl+Shift+O", lambda: self._with_target(self.open_in_default_app))


    # Startup and folder ---------------------------------------------------

    def _startup(self) -> None:
        self._update_folder_label()
        self.view.setFocus()
        self.sidebar.set_folders(self.root_strings)
        if self.roots:
            self.refresh_library()
            # Every launch runs an incremental scan to pick up files changed outside the app,
            # then the folder is watched so later changes show up by themselves.
            self.start_indexing()
            self.start_watching()
        else:
            self._update_empty_state()
        missing = []
        if not HEIC_ENABLED:
            missing.append("HEIC image support")
        if not FFMPEG_ENABLED:
            missing.append("video thumbnails and metadata")
        if missing:
            self.status.setText("Optional support unavailable: " + ", ".join(missing))

    def _update_folder_label(self) -> None:
        roots = self.roots
        if not roots:
            text = "No folder selected"
        elif len(roots) == 1:
            text = str(roots[0])
        else:
            text = f"{len(roots)} folders"
        self.folder_status.setText(text)
        self.folder_status.setToolTip("\n".join(self.root_strings))
        self.folder_label = "Add Folder" if roots else "Choose Folder"
        self._fit_toolbar()
        self.rescan_button.setEnabled(bool(roots) and not self.scanning)

    def add_folder(self) -> None:
        """Ask for a folder and add it to the library (the first one becomes the library)."""
        initial = str(self.roots[-1].parent if self.roots else Path.home() / "Pictures")
        title = "Add Folder to Library" if self.roots else "Choose Photo and Video Folder"
        selected = QFileDialog.getExistingDirectory(self, title, initial)
        if selected:
            self.add_library_folder(Path(selected))

    def add_library_folder(self, folder: Path) -> bool:
        folder = folder.resolve()
        problem = root_conflict(self.roots, folder)
        if problem:
            QMessageBox.information(self, "Can't add this folder", problem)
            return False
        first = not self.roots
        self.settings.library_folders = self.roots + [folder]  # a new list: a save may be reading the old one
        self._save_settings()
        if first:
            self.loader.reset()
            self.snapshot = None
            self.model.set_snapshot(None)
            self.sidebar.clear_albums()
        self._library_folders_changed()
        self.status.setText(f"Added “{folder.name}” to the library  ·  indexing…")
        self.start_indexing()
        return True

    def _fill_remove_folder_menu(self) -> None:
        menu = self.remove_folder_menu
        menu.clear()
        for folder in self.roots:
            menu.addAction(self.sidebar.folder_name(str(folder)),
                           lambda folder=folder: self.remove_folder(folder)).setToolTip(str(folder))
        if not self.roots:
            menu.addAction("No folders").setEnabled(False)

    def remove_folder(self, folder: Path, confirm: bool = True) -> None:
        """Stop showing a folder. Its files stay where they are; only the app's index of it is dropped."""
        folder = Path(folder)
        if folder not in self.roots:
            return
        if confirm:
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Question)
            dialog.setWindowTitle("Remove folder from library?")
            dialog.setText(f"Remove “{folder.name}” from the library?")
            dialog.setInformativeText("Its photos and videos stay in the folder on disk; they just won't "
                                      "be shown here. You can add the folder again later.")
            remove_button = dialog.addButton("Remove", QMessageBox.ButtonRole.AcceptRole)
            dialog.addButton(QMessageBox.StandardButton.Cancel)
            dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
            dialog.exec()
            if dialog.clickedButton() is not remove_button:
                return
        if self.viewer_active:
            self.close_viewer()
        # Stop the scan first, so it doesn't write rows for the folder after they are removed.
        self.stop_event.set()
        self.stop_warming()
        self.stop_people()
        self.settings.library_folders = [root for root in self.roots if root != folder]
        self.settings.hidden_albums = [entry for entry in self.hidden_albums if entry[0] != str(folder)]
        self._save_settings()
        repository = self.repository
        thread = self.index_thread

        def forget():
            if thread is not None:
                thread.join(timeout=30)
            return repository.remove_folder(folder)

        def done(_removed):
            self._library_folders_changed()
            self.status.setText(f"Removed “{folder.name}” from the library")
            if self.roots:
                self.start_indexing()  # refreshes the totals, then warming tidies up its thumbnails
            else:
                self.ready_text = "Ready"

        run_async(self.io_pool, forget, done,
                  lambda error: LOGGER.error("Could not remove %s from the index", folder, exc_info=error))

    def _library_folders_changed(self) -> None:
        """Folders were added or removed: update the sidebar, labels, grid, watchers and insights."""
        if self.scope_root is not None and Path(self.scope_root) not in self.roots:
            self.scope_kind, self.scope_root, self.scope_album = "all", None, None
        self.sidebar.set_folders(self.root_strings)
        self.sidebar.select_scope(self.scope_kind, self.scope_root, self.scope_album, notify=False)
        self._update_folder_label()
        self.refresh_library(keep_position=False)
        if self.insights_active:
            self.load_insights()
        self.start_watching()

    def _empty_action(self, action: str) -> None:
        if action == "choose":
            self.add_folder()
        elif action == "rescan":
            self.start_indexing()
        elif action == "clear_search":
            self.clear_search()
        elif action == "show_all":
            self.sidebar.select_scope("all", None, None)

    # Indexing -------------------------------------------------------------

    def start_indexing(self) -> None:
        if not self.roots:
            self.add_folder()
            return
        if self.index_thread and self.index_thread.is_alive():
            self.pending_index_start = True
            self.stop_event.set()
            self.status.setText("Stopping the current scan…")
            QTimer.singleShot(80, self.start_indexing)
            return
        self.pending_index_start = False
        self.stop_warming()  # leave the disk and CPU to the scan; warming restarts afterwards
        self.stop_people()
        self.active_scan_id += 1
        self.stop_event = threading.Event()
        roots, stop, scan_id, emit = self.roots, self.stop_event, self.active_scan_id, self.index_events.received.emit
        self.index_thread = threading.Thread(target=lambda: index_folders(roots, stop, scan_id, emit),
                                             daemon=True, name="media-indexer")
        self.index_thread.start()

    def _handle_index_event(self, event: dict) -> None:
        if event.get("scan_id") != self.active_scan_id:
            return
        event_type = event.get("type")
        if event_type == "scan_started":
            self.scanning = True
            self.progress.setRange(0, 0)
            self.progress.show()
            self.rescan_button.setEnabled(False)
            self.status.setText("Looking for photos and videos…")
            self._update_empty_state()
        elif event_type == "scan_total":
            self.progress.setRange(0, max(1, event["total"]))
        elif event_type == "scan_progress":
            self.progress.setValue(event["processed"])
            folder = ""
            if event.get("folder_count", 1) > 1:
                folder = (f"  ·  folder {event['folder_number']} of {event['folder_count']} "
                          f"({Path(event['folder']).name})")
            self.status.setText(
                f"Indexing{folder}  ·  {event['processed']:,} of {event['total']:,}  ·  "
                f"{event['updated']:,} new or changed"
            )
        elif event_type == "batch_committed":
            if not self.reload_timer.isActive():
                # Show the first items quickly, then refresh calmly while the scan continues.
                self.reload_timer.start(400 if not self.model.items else 3000)
        elif event_type in {"scan_complete", "scan_cancelled", "scan_error"}:
            self.scanning = False
            self.progress.hide()
            if self.pending_index_start:
                self.status.setText("Starting requested scan…")
                return
            if self.pending_rescan and event_type == "scan_complete":
                # Files changed while this scan ran; look again so nothing is missed.
                self.pending_rescan = False
                QTimer.singleShot(0, self.start_indexing)
            self._update_folder_label()
            if event_type == "scan_complete":
                parts = []
                if event["updated"]:
                    parts.append(f"{event['updated']:,} new or changed")
                if event["deleted"]:
                    parts.append(f"{event['deleted']:,} removed")
                if event["errors"]:
                    parts.append(f"{event['errors']:,} unreadable")
                if event.get("unreadable"):
                    count = event["unreadable"]
                    parts.append(f"{count:,} folder{'s' if count != 1 else ''} couldn't be read")
                for key, label in (("missing_folders", "not found"), ("failed_folders", "couldn't scan")):
                    if event.get(key):
                        parts.append(f"{label}: " + ", ".join(f"“{Path(folder).name}”" for folder in event[key]))
                self.scan_notes = parts
                # The scan counts every file; the sidebar query then corrects this for hidden albums.
                self.library_totals = None if self.hidden_albums else (event["total_indexed"], 0)
                self._update_ready_text(event["total_indexed"], show=True)
                self.reload_timer.stop()
                if event["updated"] or event["deleted"] or not self.snapshot:
                    self.refresh_library()
                else:
                    self.reload_sidebar()
                    self._update_empty_state()
                if self.insights_active:
                    self.load_insights()
                self.start_warming()
            elif event_type == "scan_cancelled":
                self.status.setText("Indexing cancelled")
            elif event.get("missing_folders") and not event.get("failed_folders"):
                # Every folder is missing (say, an unplugged drive): keep showing what was indexed.
                self.status.setText("Library folder not found  ·  showing what was indexed before")
                self._update_empty_state()
            elif event.get("failed_folders"):
                # A folder that can't be read keeps its index too (see MediaIndexer.run).
                self.status.setText("The library folder couldn't be read  ·  showing what was indexed before")
                self._update_empty_state()
            else:
                self.status.setText("Indexing failed")
                LOGGER.error("Indexing failed: %s", event.get("message", "Unknown error"))
                self._error("Unable to index the library",
                            "The folder may be unavailable or contain files that cannot be read. "
                            "Details were written to the application log.")

    # Library queries -----------------------------------------------------

    def query_arguments(self) -> dict:
        search = self.search_filter if self.search_filter and not self.search_filter.is_empty else None
        person = self.scope_album if self.scope_kind == "person" else None
        group = self.scope_album if self.scope_kind == "group" else None
        by_face = person is not None or group is not None
        return {"kind": "all" if by_face else self.scope_kind,
                "album": None if by_face else self.scope_album, "person": person, "group": group, "search": search,
                "oldest_first": self.settings.sort_order == "oldest", "hidden": self.hidden_albums,
                "least_typical_first": self.least_typical and person is not None}

    @property
    def single_section(self) -> str | None:
        """The grid's one heading while checking a person's faces (instead of months)."""
        if self.least_typical and self.scope_kind == "person":
            return "Least typical first  ·  wrong matches gather here"
        return None

    def refresh_library(self, keep_position: bool = True) -> None:
        """Reload the grid and the sidebar's counts and albums."""
        self.reload_library(keep_position)
        self.reload_sidebar()

    def reload_library(self, keep_position: bool = True) -> None:
        folders = self.scope_folders()
        self.query_request_id += 1
        request_id = self.query_request_id
        if not folders:
            self._apply_snapshot(None, request_id, keep_position)
            return
        arguments = self.query_arguments()
        single_section = self.single_section
        repository = self.repository
        cancel_queued(self.query_pool)
        run_async(
            self.query_pool,
            lambda: build_snapshot(repository.query(folders, **arguments), single_section),
            lambda snapshot: self._apply_snapshot(snapshot, request_id, keep_position),
            lambda error: self._query_failed(error, request_id),
        )

    def reload_sidebar(self) -> None:
        roots = self.roots
        if not roots:
            self.sidebar.clear_albums()
            return
        repository = self.repository
        hidden = self.hidden_albums
        find_people = self.settings.find_people

        def load():
            per_folder = ({str(root): repository.counts(root, hidden)["all"] for root in roots}
                          if len(roots) > 1 else {})
            everything = repository.counts(roots)["all"] if hidden else None
            people = repository.people(roots, hidden) if find_people else []
            suggestions = len(repository.face_suggestions(roots, hidden)) if people else 0
            groups = repository.person_groups(roots, hidden) if find_people else []
            return (repository.counts(roots, hidden), per_folder, repository.albums(roots), everything, people,
                    suggestions, groups)

        def apply(result):
            if roots != self.roots or hidden != self.hidden_albums:
                return
            counts, per_folder, albums, everything, people, suggestions, groups = result
            self.people_list = people
            self.sidebar.set_people(people, suggestions)
            self.groups_list = groups
            self.sidebar.set_groups(groups)
            if self.select_group_after_reload is not None:
                group, self.select_group_after_reload = self.select_group_after_reload, None
                self.sidebar.select_scope("group", None, group)
            if self.scope_kind == "person":  # the person may have been renamed
                self.remove_from_person_action.setText(f"Remove from {self.sidebar.person_name(self.scope_album)}")
            shown = counts.get("all", 0)
            self.library_totals = (shown, everything - shown if everything is not None else 0)
            self._update_ready_text()
            self.sidebar.set_counts(counts)
            self.sidebar.set_folders(self.root_strings, per_folder)
            self.sidebar.set_albums(albums, hidden)

        run_async(self.io_pool, load, apply, lambda error: LOGGER.error("Could not load albums", exc_info=error))

    def _update_ready_text(self, fallback_total: int = 0, show: bool = False) -> None:
        """"Library ready · 59 items (11 hidden) · …": counts what All Photos shows.

        Updates the status line only if it is showing the ready text (or `show` is set), so a
        newer message (say, "Hid Trips") isn't overwritten.
        """
        if not self.scan_notes and not self.ready_text.startswith("Library ready") and not show:
            return  # no scan has finished yet
        shown, hidden = self.library_totals or (fallback_total, 0)
        count = f"{shown:,} item{'s' if shown != 1 else ''}" + (f" ({hidden:,} hidden)" if hidden else "")
        showing = show or self.status.text().startswith("Library ready")
        self.ready_text = "Library ready  ·  " + "  ·  ".join([count, *self.scan_notes])
        if showing:
            self.status.setText(self.ready_text)

    def set_album_hidden(self, root: str, album: str, hide: bool = True) -> None:
        """Hide an album from All Photos, Photos, Videos, search, counts and Insights (or show it again).

        Nothing on disk changes, and the album itself still opens from Hidden Albums in the sidebar.
        """
        pair = (str(root), album)
        if hide == (pair in self.hidden_albums):
            return
        # A new list: a background settings save may be reading the old one.
        self.settings.hidden_albums = (self.hidden_albums + [pair] if hide
                                       else [entry for entry in self.hidden_albums if entry != pair])
        self._save_settings()
        if hide:
            self.status.setText(f"Hid “{album}”  ·  its photos no longer appear in All Photos. "
                                "Show it again from Hidden Albums in the sidebar.")
        else:
            self.status.setText(f"“{album}” is back in All Photos")
        self.refresh_library()
        if self.insights_active:
            self.load_insights()

    def set_scope(self, kind: str, root: str | None = None, album: str | None = None) -> None:
        """Show a library kind (all, photos, videos), one library folder, or one album of a folder."""
        if self.viewer_active:
            self.close_viewer()
        if (kind, album) != (self.scope_kind, self.scope_album):
            self.least_typical = False  # checking applies to one person at a time
            self.least_typical_action.setChecked(False)
        self.scope_kind, self.scope_root, self.scope_album = kind, root, album
        self.remove_from_person_action.setEnabled(kind == "person")
        self.remove_from_person_action.setText(
            f"Remove from {self.sidebar.person_name(album)}" if kind == "person" else "Remove from This Person")
        self.least_typical_action.setEnabled(kind == "person")
        self.reload_library(keep_position=False)

    # Search --------------------------------------------------------------

    def _apply_search(self) -> None:
        parsed = parse_query(self.search_box.text())
        if parsed == (self.search_filter or SearchFilter()):
            return
        self.search_filter = parsed
        self.reload_library(keep_position=False)

    def focus_search(self) -> None:
        if self.viewer_active:
            self.close_viewer()
        self.close_insights()
        self.close_review()
        self.search_box.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search_box.selectAll()

    def clear_search(self) -> None:
        self.search_box.clear()
        self.search_timer.stop()
        self._apply_search()
        self.view.setFocus()

    def _search_return(self) -> None:
        self.search_timer.stop()
        self._apply_search()
        self.view.setFocus()
        if not self.view.currentIndex().isValid() and self.model.rowCount():
            self.view.setCurrentIndex(self.model.index(0, 0))

    def eventFilter(self, watched, event) -> bool:
        # While typing, editing keys (⌘⌫, Delete) edit the text instead of moving photos to the Trash.
        if watched is self.search_box and event.type() == event.Type.ShortcutOverride \
                and event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            event.accept()
            return True
        # Esc in the search box clears it (a second Esc returns to the grid).
        if watched is self.search_box and event.type() == event.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            if self.search_box.text():
                self.clear_search()
            else:
                self.view.setFocus()
            return True
        return super().eventFilter(watched, event)

    def _update_sidebar_icon(self) -> None:
        self.sidebar_button.setIcon(sidebar_icon(self.theme.colors.text_muted))

    def toggle_sidebar(self) -> None:
        self.settings.sidebar_visible = not self.settings.sidebar_visible
        visible = self.settings.sidebar_visible
        for control in (self.sidebar_action, self.sidebar_button):
            control.setChecked(visible)
        self.sidebar_button.setToolTip("Hide the sidebar (⌥⌘S)" if visible else "Show the sidebar (⌥⌘S)")
        if not self.full_page_active:
            self.sidebar.setVisible(self.settings.sidebar_visible)
        self.settings_timer.start()

    def _query_failed(self, error: Exception, request_id: int) -> None:
        if request_id != self.query_request_id:
            return
        self.applied_request_id = request_id
        LOGGER.error("Library query failed", exc_info=error)
        self.status.setText("The library index could not be read. Try rescanning.")

    def _apply_snapshot(self, snapshot: LibrarySnapshot | None, request_id: int, keep_position: bool = True) -> None:
        if request_id != self.query_request_id:
            return
        self.applied_request_id = request_id
        anchor = self._anchor_item() if keep_position else None
        self.snapshot = snapshot
        self.model.set_snapshot(snapshot)
        if self.viewer_active:
            self.viewer.replace_items(self.model.items)
        self.view.reset()
        if anchor is not None:
            self._restore_anchor_item(*anchor)
        self._update_empty_state()

    def _anchor_item(self, exclude: set[int] = frozenset()) -> tuple[int, int] | None:
        """(item id, offset) of the first visible cell in All mode, to keep position across reloads.

        Items in `exclude` (about to be removed) are skipped, so the next surviving cell anchors.
        """
        if self.model.mode != "all":
            return None
        anchor = self.view.anchor()
        if anchor is None:
            return None
        row, delta = anchor
        first_rect = self.view.grid_layout.rect_for(row)
        for candidate in range(row, min(row + 400, self.model.rowCount())):
            item = self.model.item_at(candidate)
            if item is not None and item.id not in exclude:
                offset = delta + self.view.grid_layout.rect_for(candidate).y - first_rect.y
                return item.id, offset
        return None

    def _restore_anchor_item(self, item_id: int, delta: int) -> None:
        row = self.model.row_for_item_id(item_id)
        if row is not None:
            self.view.restore_anchor(row, delta)

    def _update_empty_state(self) -> None:
        if not self.roots:
            self.grid_page.show_empty(
                "Choose your photo library",
                "Pick a folder with photos and videos. It's indexed on this Mac only and nothing is uploaded.",
                "Choose Folder…", "choose")
        elif self.model.rowCount() == 0:
            searching = self.search_filter is not None and not self.search_filter.is_empty
            if self.snapshot is None or (self.scanning and not searching and self.scope_album is None
                                         and self.scope_root is None and self.scope_kind == "all"):
                self.grid_page.show_empty("Indexing your library…",
                                          "Photos and videos will appear here as they are found.")
            elif searching:
                where = f" in {self.scope_label}" if self.scope_label != "All Photos" else ""
                self.grid_page.show_empty(f"No results for “{self.search_filter.text}”{where}",
                                          "Try a different filename, or a date such as 2023, May 2023 or 2023-05-14.",
                                          "Clear Search", "clear_search")
            elif self.scope_kind == "group":
                self.grid_page.show_empty("No photos of them together",
                                          f"No shown photos have everyone in “{self.scope_label}” in them.",
                                          "Show All Photos", "show_all")
            elif self.scope_kind == "person":
                self.grid_page.show_empty("No photos of this person",
                                          f"No shown photos of “{self.scope_label}” match.",
                                          "Show All Photos", "show_all")
            elif self.scope_album is not None:
                self.grid_page.show_empty("This album is empty",
                                          f"There are no photos or videos in “{self.scope_album}” any more.",
                                          "Show All Photos", "show_all")
            elif self.scope_root is not None:
                self.grid_page.show_empty("No photos or videos",
                                          f"“{self.scope_label}” doesn't contain any supported media yet.",
                                          "Show All Photos", "show_all")
            elif self.scope_kind != "all":
                noun = "videos" if self.scope_kind == "videos" else "photos"
                self.grid_page.show_empty(f"No {noun}", f"This library doesn't contain any {noun} yet.",
                                          "Show All Photos", "show_all")
            else:
                self.grid_page.show_empty("No photos or videos",
                                          "This folder doesn't contain any supported media yet.",
                                          "Rescan", "rescan")
        else:
            self.grid_page.show_grid()
        count = len(self.model.items)
        if self.roots and not self.scanning:
            noun = "item" if count == 1 else "items"
            selected = sum(r.height() for r in self.view.selectionModel().selection()) if self.model.mode == "all" else 0
            suffix = f"  ·  {selected:,} selected" if selected > 1 else ""
            self.folder_status.setText(f"{self.scope_label}  ·  {count:,} {noun}{suffix}")
            self.folder_status.setToolTip("\n".join(self.root_strings))

    @property
    def scope_label(self) -> str:
        if self.scope_kind == "person":
            return self.sidebar.person_name(self.scope_album)
        if self.scope_kind == "group":
            return self.sidebar.group_name(self.scope_album)
        if self.scope_album is not None:
            return self.scope_album
        if self.scope_root is not None:
            return self.sidebar.folder_name(self.scope_root)
        return {"photos": "Photos", "videos": "Videos"}.get(self.scope_kind, "All Photos")

    # View modes and zoom -------------------------------------------------

    def _apply_mode_controls(self, mode: str) -> None:
        self.mode_buttons[mode].setChecked(True)
        self.mode_action_by_key[mode].setChecked(True)
        self.zoom_slider.setEnabled(mode == "all")

    def set_view_mode(self, mode: str, focus_index: int | None = None) -> None:
        """Switch Years/Months/All. Keeps roughly the same point in time on screen."""
        if self.viewer_active:
            self.close_viewer()
        self.close_insights()
        self.close_review()
        if mode != "all" and self.least_typical:
            # Years and Months group by date, which the least-typical order doesn't have.
            self.least_typical = False
            self.least_typical_action.setChecked(False)
            self.reload_library(keep_position=False)
        if focus_index is None:
            focus_index = self._focused_item_index()
        if mode != self.model.mode:
            self.model.set_mode(mode)
            self.view.set_mode(mode)
        self._apply_mode_controls(mode)
        self._update_empty_state()  # the selected count only applies to All Photos
        if focus_index is not None:
            if mode == "all":
                self.view.scroll_to_row(focus_index)
            else:
                self.view.scroll_to_row(self.model.row_for_first_index(focus_index))
        self.settings.view_mode = mode
        self.settings_timer.start()

    def set_sort_order(self, order: str) -> None:
        """Newest or oldest first, everywhere."""
        if order == self.settings.sort_order:
            self._apply_sort_controls()
            return
        self.settings.sort_order = order
        self._apply_sort_controls()
        self.settings_timer.start()
        # At the top, stay at the top (the start of the new order); further down, keep the photo in view.
        at_top = self.view.verticalScrollBar().value() == 0
        self.reload_library(keep_position=not at_top)
        if self.viewer_active:
            self.viewer.setFocus()

    def toggle_sort_order(self) -> None:
        self.set_sort_order("oldest" if self.settings.sort_order == "newest" else "newest")

    def _apply_sort_controls(self) -> None:
        oldest = self.settings.sort_order == "oldest"
        self._fit_toolbar()
        self.sort_button.setToolTip("Showing oldest first. Click to show newest first." if oldest
                                    else "Showing newest first. Click to show oldest first.")
        self.sort_action_by_key[self.settings.sort_order].setChecked(True)

    def _fit_toolbar(self) -> None:
        """Drop the text from Choose Folder, Rescan and the sort button when the full toolbar doesn't fit.

        Otherwise Qt squeezes every button below its size and clips the labels.
        """
        if self.width() < self._toolbar_width(compact=False):  # the toolbar spans the window
            self._toolbar_width(compact=True)

    def compact_toolbar_width(self) -> int:
        """Width the toolbar needs with icon-only buttons; below this Qt has to squeeze it.

        Depends on the font: Qt's offscreen platform on Windows (the UI tests) draws much wider text.
        """
        width = self._toolbar_width(compact=True)
        self._fit_toolbar()
        return width

    def _toolbar_width(self, compact: bool) -> int:
        """Show the full or icon-only labels and return the toolbar's minimum width with them."""
        oldest = self.settings.sort_order == "oldest"
        full = (self.folder_label, "Rescan", "↑  Oldest First" if oldest else "↓  Newest First")
        buttons = (self.folder_button, self.rescan_button, self.sort_button)
        for button, text in zip(buttons, ("", "", "↑" if oldest else "↓") if compact else full):
            button.setText(text)
        for button, text in zip(buttons[:2], full):
            button.setAccessibleName(text)  # still named for VoiceOver when only the icon shows
        for mark in self.zoom_marks:
            mark.setVisible(not compact)
        layout = self.toolbar.layout()
        layout.invalidate()
        return layout.minimumSize().width()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_toolbar()

    def _focused_item_index(self) -> int | None:
        """Index into the full item list of what's at the top of the screen."""
        anchor = self.view.anchor()
        if anchor is None:
            return None
        summary = self.model.summary_at(anchor[0])
        return summary.first_index if summary else anchor[0]

    def _zoom_changed(self, value: int) -> None:
        self.view.set_zoom(value)
        self.settings.zoom = value
        self.settings_timer.start()

    def step_zoom(self, direction: int) -> None:
        if self.viewer_active:
            self.viewer.canvas.zoom_in() if direction > 0 else self.viewer.canvas.zoom_out()
        elif self.model.mode == "all":
            self.zoom_slider.setValue(int(self.zoom_slider.value() * (1.2 if direction > 0 else 1 / 1.2)))

    # Opening items -------------------------------------------------------

    def _open_index(self, index) -> None:
        summary = self.model.summary_at(index.row())
        if summary is not None:
            # Years -> Months -> All, like Photos.
            self.set_view_mode("months" if self.model.mode == "years" else "all", summary.first_index)
            return
        if self.model.item_at(index.row()) is not None:
            self.open_viewer(index.row())

    @property
    def viewer_active(self) -> bool:
        return self.stack.currentWidget() is self.viewer

    @property
    def full_page_active(self) -> bool:
        """The viewer, Insights or Review fills the window (no toolbar or sidebar)."""
        return self.stack.currentWidget() is not self.grid_page

    def open_current(self) -> None:
        index = self.view.currentIndex()
        if not (self.viewer_active or self.insights_active or self.review_active) and index.isValid():
            self._open_index(index)

    def open_viewer(self, row: int) -> None:
        """Show the item at `row` (All Photos mode) full-window. The grid stays as it was."""
        item = self.model.item_at(row)
        self._viewer_return = (item.id if item else None, self.view.verticalScrollBar().value())
        self.toolbar.hide()
        self.sidebar.hide()
        self.stack.setCurrentWidget(self.viewer)
        self.viewer.set_faces_available(self.settings.find_people and FACES_ENABLED)
        self.viewer.show_items(self.model.items, row)

    def close_viewer(self) -> None:
        if not self.viewer_active:
            return
        item = self.viewer.current_item
        self.viewer.stop()
        self.stack.setCurrentWidget(self.grid_page)
        self.toolbar.show()
        self.sidebar.setVisible(self.settings.sidebar_visible)
        self.info_panel.setVisible(self.settings.info_panel_visible)
        self.info_timer.start()
        row = self.model.row_for_item_id(item.id) if item else None
        if row is not None:
            index = self.model.index(row, 0)
            # Select the last viewed item; scrolls only if it is off screen, so the grid is where you left it.
            self.view.selectionModel().setCurrentIndex(
                index, self.view.selectionModel().SelectionFlag.ClearAndSelect)
            opened_id, scroll = self._viewer_return
            if item.id == opened_id:
                self.view.verticalScrollBar().setValue(scroll)
            else:
                self.view.scrollTo(index)
        self.view.setFocus()

    def toggle_actual_size(self) -> None:
        if self.viewer_active:
            self.viewer.canvas.toggle_actual_size()

    def open_in_default_app(self, item) -> None:
        self._run_file_command(lambda: open_in_default_app(item.path), "Unable to open file")

    def reveal_item(self, item) -> None:
        self._run_file_command(lambda: reveal_in_file_manager(item.path), "Unable to show the file")

    def _run_file_command(self, command, title: str) -> None:
        def done(result):
            success, detail = result
            if not success:
                self._error(title, "The file may have moved or you may not have permission to access it.\n\n"
                            + (detail or ""))

        run_async(self.io_pool, command, done)

    # Folder watching -------------------------------------------------------

    def start_watching(self) -> None:
        """Watch every library folder for changes (if enabled), replacing any earlier watchers."""
        self.stop_watching()
        if not self.roots or not self.settings.watch_folder:
            return
        emit = self.watch_events.received.emit
        for folder in self.roots:
            watcher = LibraryWatcher(folder, lambda: emit(None))
            if watcher.start():
                self.watchers.append(watcher)
            else:
                # Usually an unplugged drive; try them all again in a while.
                self.watch_retry.start()

    def stop_watching(self) -> None:
        self.watch_retry.stop()
        for watcher in self.watchers:
            watcher.stop()
        self.watchers = []

    def _folder_changed(self, _payload=None) -> None:
        """Files changed in a library folder: run the (incremental) scan once things settle."""
        if not self.roots:
            return
        if self.scanning:
            self.pending_rescan = True
            return
        self.status.setText("Changes found in the library folder  ·  updating…")
        self.start_indexing()

    def _move_to_trash(self, path: Path) -> bool:
        """Runs on a worker thread. The watcher is told first, so the app's own change isn't a "new" change."""
        for watcher in list(self.watchers):
            watcher.ignore([path])
        return QFile.moveToTrash(str(path))

    # Thumbnail warming -----------------------------------------------------

    def start_warming(self) -> None:
        """Create every missing thumbnail in the background, newest first.

        While a video plays this only notes the request; it runs once playback pauses or stops.
        """
        folders = self.roots
        if not folders:
            return
        self.stop_warming()
        self.warm_pending = True
        if self.video_playing:
            return
        self.warm_id += 1
        warm_id = self.warm_id
        stop = self.warm_stop = threading.Event()
        repository = self.repository
        emit = self.warm_events.received.emit

        def run():
            try:
                items = repository.query(folders)
                result = warm_thumbnails(
                    items, stop, lambda done, total: emit({"type": "progress", "id": warm_id, "done": done,
                                                           "total": total}))
                if not stop.is_set():
                    # Then the large covers for the Years and Months tiles (one per year and month).
                    snapshot = build_snapshot(items)
                    covers = list({summary.cover.id: summary.cover
                                   for summary in snapshot.years + snapshot.months}.values())
                    warm_thumbnails(covers, stop, edge=COVER_EDGE)
                # Finally drop thumbnails of files that were deleted, edited or moved.
                pruned = None
                if not stop.is_set():
                    pruned = prune_thumbnail_cache(media_module.THUMBNAIL_DIR, repository.cache_entries(),
                                                   stop_event=stop)
                    if pruned.removed:
                        LOGGER.info("Removed %d stale thumbnails (%d bytes)", pruned.removed, pruned.freed_bytes)
                if stop.is_set():
                    emit({"type": "cancelled", "id": warm_id})
                else:
                    emit({"type": "done", "id": warm_id, "result": result, "pruned": pruned})
            except Exception as exc:  # reported on the UI thread
                emit({"type": "error", "id": warm_id, "message": str(exc)})

        threading.Thread(target=run, daemon=True, name="thumbnail-warmer").start()

    def stop_warming(self) -> None:
        self.warm_stop.set()

    def _playback_state_changed(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        if playing == self.video_playing:
            return
        self.video_playing = playing
        if playing:
            if self.warm_pending:
                self.stop_warming()  # stays pending; resumes when playback stops
                self.progress.hide()
            if self.people_pending:
                self.stop_people()
                self.progress.hide()
        elif self.warm_pending and not self.scanning:
            self.start_warming()
        elif self.people_pending and not self.scanning:
            self.start_people()

    def _handle_warm_event(self, event: dict) -> None:
        if event.get("id") != self.warm_id or self.scanning:
            return
        if event["type"] == "cancelled":
            self.progress.hide()  # still pending; whoever stopped it restarts it
            return
        if event["type"] == "progress":
            done, total = event["done"], event["total"]
            self.progress.setRange(0, max(1, total))
            self.progress.setValue(done)
            self.progress.show()
            self.status.setText(f"Preparing thumbnails  ·  {done:,} of {total:,}  ·  scrolling gets faster as this finishes")
        elif event["type"] == "done":
            self.warm_pending = False
            result: WarmResult = event["result"]
            self.progress.hide()
            pruned = event.get("pruned")
            notes = []
            if result.missing and not result.cancelled:
                notes.append("all thumbnails ready")
                if result.failed:
                    notes.append(f"{result.failed:,} without a preview")
            if pruned and pruned.removed:
                notes.append(f"freed {format_size(pruned.freed_bytes)} of old thumbnails")
            if notes:
                self.status.setText(self.ready_text + "  ·  " + "  ·  ".join(notes))
            self.start_people()
        else:
            self.warm_pending = False
            self.progress.hide()
            LOGGER.error("Thumbnail preparation failed: %s", event.get("message"))

    # People ----------------------------------------------------------------

    @property
    def models_dir(self) -> Path:
        return database_module.APP_DIR / "models"

    def start_people(self) -> None:
        """Find faces in photos not analysed yet, then regroup people. Off unless the setting is on."""
        if not (self.settings.find_people and FACES_ENABLED and self.roots):
            return
        self.stop_people()
        self.people_pending = True
        if self.video_playing or self.scanning or self.warm_pending:
            return  # resumes when playback stops or after the scan and warming
        self.people_id += 1
        people_id = self.people_id
        stop = self.people_stop = threading.Event()
        repository, folders, models, factory = self.repository, self.roots, self.models_dir, self.face_engine_factory
        emit = self.people_events.received.emit

        def make_engine():
            if factory is OpenCVFaceEngine and not models_present(models):
                emit({"type": "downloading", "id": people_id})
                ensure_models(models)
            return factory(models)

        def run():
            try:
                result, groups = run_people_pass(
                    repository, folders, make_engine, stop,
                    lambda done, total: emit({"type": "progress", "id": people_id, "done": done, "total": total}),
                    load_image=decode_for_faces)
                emit({"type": "cancelled" if groups is None else "done", "id": people_id, "result": result,
                      "groups": groups})
            except ModelError as exc:
                emit({"type": "error", "id": people_id, "message": str(exc)})
            except Exception as exc:  # reported on the UI thread
                LOGGER.exception("Finding people failed")
                emit({"type": "error", "id": people_id, "message": str(exc)})

        threading.Thread(target=run, daemon=True, name="people").start()

    def stop_people(self) -> None:
        self.people_stop.set()

    def _handle_people_event(self, event: dict) -> None:
        if event.get("id") != self.people_id or self.scanning:
            return
        kind = event["type"]
        if kind == "downloading":
            self.status.setText("Downloading the face models (about 38 MB, once)…")
        elif kind == "progress":
            done, total = event["done"], event["total"]
            self.progress.setRange(0, max(1, total))
            self.progress.setValue(done)
            self.progress.show()
            self.status.setText(f"Finding people  ·  {done:,} of {total:,} photos")
        elif kind == "cancelled":
            self.progress.hide()
        elif kind == "done":
            self.people_pending = False
            self.progress.hide()
            if event["result"].analyzed:
                self.status.setText(self.ready_text + f"  ·  people updated from {event['result'].analyzed:,} photos")
            self.reload_sidebar()
            if self.scope_kind in ("person", "group"):
                self.reload_library()
        else:
            self.people_pending = False
            self.progress.hide()
            self.status.setText(f"Couldn't find people: {event.get('message')}")
            LOGGER.error("Finding people failed: %s", event.get("message"))

    def rename_person(self, person_id: int) -> None:
        current = self.sidebar.person_name(person_id)
        name, accepted = QInputDialog.getText(self, "Name This Person", "Name:",
                                              text="" if current == UNNAMED else current)
        if accepted:
            self._change_people(lambda repository: repository.rename_person(person_id, name))

    def merge_people(self, keep: int, other: int) -> None:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setWindowTitle("Merge people?")
        dialog.setText(f"Merge “{self.sidebar.person_name(other)}” into “{self.sidebar.person_name(keep)}”?")
        dialog.setInformativeText("Their photos are combined under one person. Your photo files are not changed.")
        merge = dialog.addButton("Merge", QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        dialog.exec()
        if dialog.clickedButton() is merge:
            if self.scope_kind == "person" and self.scope_album == other:
                self.sidebar.select_scope("person", None, keep, notify=False)
                self.scope_album = keep
            self._change_people(lambda repository: repository.merge_people(keep, other))

    def set_person_hidden(self, person_id: int, hide: bool) -> None:
        self._change_people(lambda repository: repository.set_person_hidden(person_id, hide))

    def remove_from_person(self) -> None:
        """"Not Ana": take the selected photos (or the one in the viewer) out of the person shown.

        Only that person's face in each photo is affected; the faces remember they aren't this
        person, so regrouping won't put them back, and they may still join whoever they really are.
        """
        if self.scope_kind != "person":
            return
        person, ids = self.scope_album, [item.id for item in self.target_items()]
        if not ids:
            self.status.setText("Select the photos that aren't this person")
            return
        name = self.sidebar.person_name(person)

        def done(_result):
            self.status.setText(f"Removed {len(ids):,} photo{'s' if len(ids) != 1 else ''} from {name}")
            self.refresh_library()

        repository = self.repository
        run_async(self.io_pool, lambda: repository.detach_faces(person, ids), done,
                  lambda error: LOGGER.error("Could not update people", exc_info=error))

    # Faces in the viewer --------------------------------------------------

    def _load_faces(self, item) -> None:
        repository = self.repository
        run_async(self.io_pool, lambda: repository.faces_in_photo(item.id),
                  lambda faces: self.viewer.set_faces(item.id, faces),
                  lambda error: LOGGER.error("Could not load faces", exc_info=error))

    def _face_menu(self, face, position) -> None:
        """What to do with one face: confirm it, say who it is instead, or that it isn't this person."""
        name = face.name or UNNAMED
        menu = QMenu(self)
        if face.person_id is not None:
            heading = menu.addAction(f"{name}" + ("  ✓" if face.confirmed else ""))
            heading.setEnabled(False)
            if not face.confirmed:
                menu.addAction(f"Yes, This Is {name}",
                               lambda: self._change_face(lambda repository: repository.assign_face(face.id, face.person_id)))
            menu.addAction(f"Not {name}",
                           lambda: self._change_face(lambda repository: repository.reject_face(face.id, face.person_id)))
            menu.addSeparator()
        others = [person for person in self.people_list if person.id != face.person_id]
        if others:
            choose = menu.addMenu("This Is…")
            for person in sorted(others, key=lambda entry: (not entry.name, entry.name.casefold(), -entry.count)):
                label = person.name or f"{UNNAMED} ({person.count:,} photos)"
                choose.addAction(label, lambda person=person: self._change_face(
                    lambda repository: repository.assign_face(face.id, person.id)))
        menu.addAction("New Person…", lambda: self._name_face(face))
        self._exec_menu(menu, position)

    def _exec_menu(self, menu: QMenu, position) -> None:
        """Show a context menu. Separate so tests can choose an action instead of a person."""
        menu.exec(position)

    def _name_face(self, face) -> None:
        name, accepted = QInputDialog.getText(self, "New Person", "Name:")
        if accepted and name.strip():
            self._change_face(lambda repository: repository.new_person_for_face(face.id, name))

    def _change_face(self, change) -> None:
        """Apply a change to one face, then redraw the viewer's faces and refresh people."""
        item = self.viewer.current_item
        repository = self.repository

        def done(_result):
            if item is not None:
                self._load_faces(item)
            self.refresh_library()

        run_async(self.io_pool, lambda: change(repository), done,
                  lambda error: LOGGER.error("Could not update the face", exc_info=error))

    # Review People ------------------------------------------------------------

    @property
    def review_active(self) -> bool:
        return self.stack.currentWidget() is self.review

    def show_review(self) -> None:
        if not (self.settings.find_people and self.roots):
            return
        if self.viewer_active:
            self.close_viewer()
        self.close_insights()
        if not self.review_active:
            self.toolbar.hide()
            self.sidebar.hide()
            self.info_panel.hide()
            self.stack.setCurrentWidget(self.review)
        self.review.setFocus()
        self.review.show_loading()
        self.review_request_id += 1
        request_id, repository, roots, hidden = self.review_request_id, self.repository, self.roots, self.hidden_albums

        def done(suggestions):
            if request_id == self.review_request_id and self.review_active:
                covers = {person.id: (person.cover, person.box) for person in self.people_list}
                self.review.set_suggestions(suggestions, covers)

        run_async(self.io_pool, lambda: repository.face_suggestions(roots, hidden), done,
                  lambda error: LOGGER.error("Could not load face suggestions", exc_info=error))

    def close_review(self) -> None:
        if not self.review_active:
            return
        self.stack.setCurrentWidget(self.grid_page)
        self.toolbar.show()
        self.sidebar.setVisible(self.settings.sidebar_visible)
        self.info_panel.setVisible(self.settings.info_panel_visible)
        self.view.setFocus()
        if self.review.answers:
            self.refresh_library()  # people's photos and counts changed

    def _review_answered(self, yes: bool, suggestion) -> None:
        face, person = suggestion.face_id, suggestion.person_id
        if yes:
            self._change_people(lambda repository: repository.assign_face(face, person), refresh=False)
        else:
            self._change_people(lambda repository: repository.reject_face(face, person), refresh=False)

    def set_least_typical(self, on: bool) -> None:
        """Show the person's photos with the least typical face first, as one list in All Photos."""
        on = on and self.scope_kind == "person"
        self.least_typical = on
        self.least_typical_action.setChecked(on)
        if on and self.model.mode != "all":
            self.set_view_mode("all")
        self.reload_library(keep_position=False)

    def check_person(self, person_id: int) -> None:
        self.sidebar.select_scope("person", None, person_id)
        self.set_least_typical(True)

    # Groups of people ------------------------------------------------------

    def _open_group_dialog(self, title: str, name: str = "", members=()) -> tuple[str, list[int]] | None:
        """Ask for a group's name and people. Separate so tests can answer it."""
        dialog = GroupDialog(self.people_list, name, members, title, self)
        return (dialog.selected_name, dialog.selected_members) if dialog.exec() else None

    def new_group(self, members) -> None:
        if not self.settings.find_people:
            return
        answer = self._open_group_dialog("New Group", members=members)
        if answer is None:
            return
        name, chosen = answer
        repository = self.repository

        def done(group_id):
            self.select_group_after_reload = group_id
            self.refresh_library()

        run_async(self.io_pool, lambda: repository.create_group(name, chosen), done,
                  lambda error: LOGGER.error("Could not create the group", exc_info=error))

    def edit_group(self, group_id: int) -> None:
        group = next((entry for entry in self.groups_list if entry.id == group_id), None)
        if group is None:
            return
        answer = self._open_group_dialog("Edit Group", group.name, group.members)
        if answer is not None:
            name, chosen = answer
            # Members with no photo shown right now (say, only in a hidden album) aren't in the dialog;
            # they stay in the group rather than being dropped unseen.
            offered = {person.id for person in self.people_list}
            chosen = list(chosen) + [member for member in group.members if member not in offered]
            self._change_people(lambda repository: repository.update_group(group_id, name, chosen))

    def rename_group(self, group_id: int) -> None:
        name, accepted = QInputDialog.getText(self, "Rename Group", "Name:", text=self.sidebar.group_name(group_id))
        if accepted and name.strip():
            self._change_people(lambda repository: repository.update_group(group_id, name=name))

    def delete_group(self, group_id: int) -> None:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setWindowTitle("Delete group?")
        dialog.setText(f"Delete the group “{self.sidebar.group_name(group_id)}”?")
        dialog.setInformativeText("Only the group goes. Its people and their photos stay as they are.")
        delete = dialog.addButton("Delete Group", QMessageBox.ButtonRole.DestructiveRole)
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        dialog.exec()
        if dialog.clickedButton() is delete:
            self._change_people(lambda repository: repository.delete_group(group_id))

    def _change_people(self, change, refresh: bool = True) -> None:
        repository = self.repository
        run_async(self.io_pool, lambda: change(repository), lambda _result: self.refresh_library() if refresh else None,
                  lambda error: LOGGER.error("Could not update people", exc_info=error))

    # Library Insights ------------------------------------------------------

    @property
    def insights_active(self) -> bool:
        return self.stack.currentWidget() is self.insights

    def show_insights(self) -> None:
        if self.viewer_active:
            self.close_viewer()
        self.close_review()
        if not self.insights_active:
            self.toolbar.hide()
            self.sidebar.hide()
            self.info_panel.hide()
            self.stack.setCurrentWidget(self.insights)
        self.insights.setFocus()
        self.load_insights()

    def close_insights(self) -> None:
        if not self.insights_active:
            return
        self.stack.setCurrentWidget(self.grid_page)
        self.toolbar.show()
        self.sidebar.setVisible(self.settings.sidebar_visible)
        self.info_panel.setVisible(self.settings.info_panel_visible)
        self.view.setFocus()

    def load_insights(self) -> None:
        """Query the index on a worker thread; only the latest request's result is shown."""
        self.insights_request_id += 1
        request_id = self.insights_request_id
        folders = self.roots
        if not folders:
            self.insights.set_folder_name("")
            self.insights.show_no_folder()
            return
        self.insights.set_folder_name(folders[0].name if len(folders) == 1 else f"{len(folders)} folders")
        self.insights.show_loading()
        repository = self.repository

        def done(result):
            if request_id == self.insights_request_id and folders == self.roots:
                self.insights.set_result(result)

        def failed(error):
            if request_id == self.insights_request_id:
                LOGGER.error("Could not load Library Insights", exc_info=error)
                self.insights.show_error()

        hidden = self.hidden_albums
        run_async(self.io_pool, lambda: repository.library_analytics(folders, hidden), done, failed)

    # Selection, info and trash ---------------------------------------------

    def selected_items(self) -> list:
        """Selected items in All Photos mode, in grid order (walks ranges, not every index)."""
        if self.model.mode != "all":
            return []
        items = self.model.items
        rows = []
        for selection_range in self.view.selectionModel().selection():
            rows.extend(range(selection_range.top(), selection_range.bottom() + 1))
        return [items[row] for row in sorted(set(rows)) if row < len(items)]

    def target_items(self) -> list:
        """What an action applies to: the viewer's item, else the grid selection."""
        if self.viewer_active:
            item = self.viewer.current_item
            return [item] if item else []
        if self.full_page_active:
            return []  # Insights or Review: the grid's selection isn't on screen
        return self.selected_items()

    def _with_target(self, callback) -> None:
        items = self.target_items()
        if len(items) == 1:
            callback(items[0])
        elif not items:
            self.status.setText("Select a photo or video first")
        else:
            self.status.setText("Select a single item for this")

    def _update_info(self) -> None:
        if self.viewer_active:
            return
        selection = self.view.selectionModel()
        if self.model.mode != "all":
            current = self.view.currentIndex()
            summary = self.model.summary_at(current.row()) if current.isValid() and selection.isSelected(current) else None
            if summary is not None:
                self.info_panel.show_summary(summary.title if self.model.mode == "years" else
                                             f"{summary.title} {summary.key[0]}" if summary.key else summary.title,
                                             summary.subtitle)
            else:
                self.info_panel.show_items([])
            self._update_empty_state()
            return
        items = self.selected_items()
        self.info_panel.show_items(items)
        self._update_empty_state()

    def toggle_info(self) -> None:
        visible = not self.settings.info_panel_visible
        self.settings.info_panel_visible = visible
        self.info_panel.setVisible(visible and not (self.insights_active or self.review_active))
        for control in (self.info_action, self.info_button, self.viewer_info_button):
            control.setChecked(visible)
        self.settings_timer.start()

    def select_all(self) -> None:
        if self.search_box.hasFocus():
            self.search_box.selectAll()
        elif not self.viewer_active and self.model.mode == "all":
            self.view.selectAll()

    def _grid_context_menu(self, position) -> None:
        index = self.view.indexAt(position)
        if not index.isValid() or self.model.mode != "all":
            return
        selection = self.view.selectionModel()
        if not selection.isSelected(index):
            selection.setCurrentIndex(index, selection.SelectionFlag.ClearAndSelect)
        items = self.selected_items()
        menu = QMenu(self)
        single = len(items) == 1
        menu.addAction("Open", lambda: self._open_index(index)).setEnabled(single)
        menu.addAction("Get Info", lambda: None if self.settings.info_panel_visible else self.toggle_info())
        menu.addSeparator()
        menu.addAction(REVEAL_LABEL, lambda: self.reveal_item(items[0])).setEnabled(single)
        menu.addAction("Open in Default App", lambda: self.open_in_default_app(items[0])).setEnabled(single)
        menu.addSeparator()
        if self.scope_kind == "person":
            name = self.sidebar.person_name(self.scope_album)
            menu.addAction(f"Not {name} – Remove from {name}" if single
                           else f"Not {name} – Remove {len(items):,} Photos from {name}", self.remove_from_person)
            menu.addSeparator()
        label = "Move to Trash…" if single else f"Move {len(items):,} Items to Trash…"
        menu.addAction(label, self.move_to_trash)
        self._exec_menu(menu, self.view.viewport().mapToGlobal(position))

    def move_to_trash(self) -> None:
        items = self.target_items()
        folders = self.roots
        if not items or not folders:
            self.status.setText("Select photos or videos to move to the Trash")
            return
        if self.trash_in_progress:
            return
        videos = sum(1 for item in items if item.is_video)
        photos = len(items) - videos
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        if len(items) == 1:
            kind = "video" if videos else "photo"
            dialog.setWindowTitle(f"Move {kind} to Trash?")
            dialog.setText(f"Move “{items[0].name}” to the Trash?")
        else:
            parts = [f"{photos:,} photo{'s' if photos != 1 else ''}"] if photos else []
            if videos:
                parts.append(f"{videos:,} video{'s' if videos != 1 else ''}")
            dialog.setWindowTitle("Move items to Trash?")
            dialog.setText(f"Move {len(items):,} items ({' and '.join(parts)}) to the Trash?")
        live = sum(1 for item in items if item.is_live)
        live_note = ""
        if live:
            live_note = (" Its Live Photo video is moved too." if len(items) == 1 else
                         f" The videos of {live:,} Live Photo{'s' if live != 1 else ''} are moved too.")
        dialog.setInformativeText("The original files are moved out of your library."
                                  f"{live_note} You can restore them from the Trash.")
        move_button = dialog.addButton("Move to Trash", QMessageBox.ButtonRole.DestructiveRole)
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        dialog.exec()
        if dialog.clickedButton() is not move_button:
            return

        if self.viewer_active:
            # Release a playing video so the file can be moved.
            self.viewer.video.stop()
        first_row = self.model.row_for_item_id(items[0].id) if self.model.mode == "all" else None
        self.trash_in_progress = True
        self.status.setText(f"Moving {len(items):,} item{'s' if len(items) != 1 else ''} to the Trash…")
        repository = self.repository
        run_async(
            self.io_pool,
            lambda: trash_items(folders, items, lambda path: self._move_to_trash(path), repository),
            lambda result: self._trash_finished(result, first_row),
            self._trash_failed,
        )

    def _trash_failed(self, error: Exception) -> None:
        self.trash_in_progress = False
        LOGGER.error("Move to Trash failed", exc_info=error)
        self._error("Unable to move to Trash", "Something went wrong. Details were written to the application log.")

    def _trash_finished(self, result: TrashResult, first_row: int | None) -> None:
        self.trash_in_progress = False
        removed = result.trashed_ids
        if removed:
            self.loader.forget(removed)
            count = len(removed)
            self.status.setText(f"Moved {count:,} item{'s' if count != 1 else ''} to the Trash")
            if result.unpaired:
                self.refresh_library()  # a Live Photo's clip stayed behind and is now a video of its own
            else:
                self._remove_from_grid(removed, first_row)
                self.reload_sidebar()
        if result.failed:
            names = "\n".join(f"• {item.name}: {reason}" for item, reason in result.failed[:8])
            more = f"\n…and {len(result.failed) - 8:,} more" if len(result.failed) > 8 else ""
            self._error("Some items were not moved",
                        f"{len(result.failed):,} item(s) could not be moved to the Trash:\n\n{names}{more}")
        elif self.viewer_active and self.viewer.current_item and self.viewer.current_item.is_video \
                and self.viewer.current_item.id not in removed:
            self.viewer.go_to(self.viewer.index)

    def _remove_from_grid(self, removed: set[int], first_row: int | None) -> None:
        """Drop trashed items without re-querying; keep the scroll position and select the next item."""
        snapshot = self.snapshot
        if snapshot is None:
            return
        if self.applied_request_id != self.query_request_id:
            # A newer query (another scope or search) is on its way; trimming the old snapshot would
            # cancel it. The index no longer has the trashed rows, so query again instead.
            self.reload_library()
            return
        self.query_request_id += 1
        request_id = self.query_request_id
        anchor = self._anchor_item(exclude=removed)
        single_section = self.single_section
        remaining_source = snapshot.items

        def rebuilt(new_snapshot):
            if request_id != self.query_request_id:
                return
            self._apply_snapshot(new_snapshot, request_id, keep_position=False)
            if anchor is not None:
                self._restore_anchor_item(*anchor)
            if first_row is not None and self.model.rowCount() and not self.viewer_active:
                index = self.model.index(min(first_row, self.model.rowCount() - 1), 0)
                self.view.selectionModel().setCurrentIndex(index, self.view.selectionModel().SelectionFlag.ClearAndSelect)
                self.view.scrollTo(index)

        run_async(self.query_pool,
                  lambda: build_snapshot([item for item in remaining_source if item.id not in removed], single_section),
                  rebuilt)

    # Settings ------------------------------------------------------------

    def _sidebar_sections_changed(self, collapsed: list[str]) -> None:
        self.settings.collapsed_sections = list(collapsed)
        self._save_settings()

    def _save_settings(self) -> None:
        snapshot = dataclasses.replace(self.settings)
        store = self.settings_store
        ticket = store.ticket()  # taken here, so a slower older save can't overwrite this one
        run_async(self.io_pool, lambda: store.save(snapshot, ticket), lambda _result: None,
                  lambda error: LOGGER.error("Could not save settings", exc_info=error))

    def show_preferences(self) -> None:
        dialog = PreferencesDialog(self.settings.theme, self.settings.time_format, self.settings.watch_folder, self,
                                   find_people=self.settings.find_people, faces_available=FACES_ENABLED)
        if dialog.exec():
            if dialog.selected_find_people != self.settings.find_people:
                self.settings.find_people = dialog.selected_find_people
                self.review_action.setEnabled(self.settings.find_people)
                self.new_group_action.setEnabled(self.settings.find_people)
                self.viewer.set_faces_available(self.settings.find_people)
                if self.settings.find_people:
                    self.start_people()
                else:
                    self.stop_people()
                    self.people_pending = False
                    if self.scope_kind in ("person", "group"):
                        self.sidebar.select_scope("all", None, None)
                self.reload_sidebar()
            self.settings.theme = dialog.selected_theme
            self.settings.time_format = dialog.selected_time_format
            if dialog.selected_watch_folder != self.settings.watch_folder:
                self.settings.watch_folder = dialog.selected_watch_folder
                self.start_watching()
            self.theme.apply(self.settings.theme)
            self.viewer.set_hour12(uses_12_hour(self.settings.time_format))
            self.info_panel.set_hour12(uses_12_hour(self.settings.time_format))
            self._save_settings()

    def _error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.stop_watching()
        self.stop_event.set()
        self.stop_warming()
        self.stop_people()
        self.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self._save_settings()
        cancel_queued(self.query_pool)
        self.io_pool.waitForDone(2000)
        self.viewer.shutdown()
        self.loader.shutdown()
        event.accept()
