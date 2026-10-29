"""Notice changes in the library folder and ask for a rescan once things settle.

macOS reports file-system changes through FSEvents (via the `watchdog` package). Copying a
batch of photos produces thousands of events, and a large video is written over many
seconds, so changes are collected and reported once the folder has been quiet for a moment
and no file that changed is still growing.
"""

import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable

from .constants import SUPPORTED_EXTENSIONS

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer
    WATCHDOG_ENABLED = True
except ImportError:  # the app still works; it just needs ⌘R to see changes
    FileSystemEventHandler = object
    Observer = None
    WATCHDOG_ENABLED = False

LOGGER = logging.getLogger(__name__)
QUIET_SECONDS = 3.0      # report once nothing has changed for this long
MAX_WAIT_SECONDS = 60.0  # ...but never hold changes back longer than this
TRACKED_FILES = 200      # how many changed files are checked for "still being written"
# FSEvents replays a few events from just before watching began; the scan that runs at
# start-up covers those, so they are ignored.
STARTUP_GRACE_SECONDS = 1.0


class ChangeBatcher:
    """Decides when a burst of changes is over. Pure logic; the caller supplies the time."""

    def __init__(self, quiet_seconds: float = QUIET_SECONDS, max_wait_seconds: float = MAX_WAIT_SECONDS):
        self.quiet_seconds = quiet_seconds
        self.max_wait_seconds = max_wait_seconds
        self._first: float | None = None
        self._last = 0.0
        self._sizes: dict[str, int | None] = {}

    @property
    def pending(self) -> bool:
        return self._first is not None

    def note(self, now: float, path: str | None = None, size: int | None = None) -> None:
        if self._first is None:
            self._first = now
        self._last = now
        if path is not None and (path in self._sizes or len(self._sizes) < TRACKED_FILES):
            self._sizes[path] = size

    def due(self, now: float, size_of: Callable[[str], int | None]) -> bool:
        """True (and reset) when the changes should be reported now."""
        if self._first is None:
            return False
        overdue = now - self._first >= self.max_wait_seconds
        if not overdue:
            if now - self._last < self.quiet_seconds:
                return False
            # Quiet, but a file that changed may still be growing (a big copy in progress).
            for path, size in list(self._sizes.items()):
                current = size_of(path)
                if current != size:
                    self.note(now, path, current)
                    return False
        self._first = None
        self._sizes.clear()
        return True


def _relevant(path: str, is_directory: bool) -> bool:
    if any(part.startswith(".") for part in Path(path).parts[1:]):
        return False  # hidden files and folders (.DS_Store, .Trashes, ._AppleDouble)
    return is_directory or os.path.splitext(path)[1].lower() in SUPPORTED_EXTENSIONS


def _size(path: str) -> int | None:
    try:
        return os.stat(path).st_size
    except OSError:
        return None


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: "LibraryWatcher"):
        super().__init__()
        self.watcher = watcher

    def on_any_event(self, event: "FileSystemEvent") -> None:
        if event.event_type in {"opened", "closed_no_write"}:
            return  # reads, not changes
        if event.is_directory and event.event_type == "modified":
            return  # FSEvents reports this for the parent of every changed file
        paths = [os.fsdecode(event.src_path)]
        if getattr(event, "dest_path", ""):
            paths.append(os.fsdecode(event.dest_path))
        for path in paths:
            if _relevant(path, event.is_directory):
                self.watcher.notice(path, event.is_directory)


class LibraryWatcher:
    """Watches one folder tree; calls `on_change()` on its own thread after changes settle."""

    def __init__(self, folder: Path, on_change: Callable[[], None],
                 quiet_seconds: float = QUIET_SECONDS, max_wait_seconds: float = MAX_WAIT_SECONDS):
        self.folder = Path(folder)
        self.on_change = on_change
        self._batcher = ChangeBatcher(quiet_seconds, max_wait_seconds)
        self._lock = threading.Lock()
        self._ignored: dict[str, float] = {}
        self._observer = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0

    def start(self) -> bool:
        """Start watching. Returns False if watching isn't possible (missing folder or package)."""
        if not WATCHDOG_ENABLED or not self.folder.is_dir():
            return False
        try:
            observer = Observer()
            observer.schedule(_Handler(self), str(self.folder), recursive=True)
            observer.start()
        except OSError as exc:
            LOGGER.warning("Cannot watch %s: %s", self.folder, exc)
            return False
        self._observer = observer
        self._started = time.monotonic()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="library-watcher")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=2)
            except RuntimeError:
                pass
            self._observer = None

    @property
    def running(self) -> bool:
        return self._observer is not None and self._observer.is_alive()

    def ignore(self, paths, seconds: float = 15.0) -> None:
        """Don't report these paths for a while (changes the app itself makes, like Move to Trash)."""
        until = time.monotonic() + seconds
        with self._lock:
            for path in paths:
                self._ignored[os.path.realpath(path)] = until

    def notice(self, path: str, is_directory: bool = False) -> None:
        now = time.monotonic()
        if now - self._started < STARTUP_GRACE_SECONDS:
            return
        with self._lock:
            until = self._ignored.get(os.path.realpath(path))
            if until is not None:
                if now < until:
                    return
                del self._ignored[os.path.realpath(path)]
            self._batcher.note(now, None if is_directory else path, None if is_directory else _size(path))

    def _run(self) -> None:
        while not self._stop.wait(0.5):
            with self._lock:
                due = self._batcher.due(time.monotonic(), _size)
            if due:
                try:
                    self.on_change()
                except Exception:
                    LOGGER.exception("Folder change callback failed")
