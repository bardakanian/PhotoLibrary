"""Create every missing thumbnail in the background, so scrolling never waits on an original.

Creating a thumbnail means decoding the full photo or a video frame (about 50 ms for a
12 MP JPEG, about 450 ms for a HEIC), while reading an existing one takes about 1 ms.
Doing the slow part ahead of time is what makes the grid feel instant. Threads are
enough: Pillow and libheif release the GIL while decoding (measured: worker processes
were no faster, and only added start-up time).
"""

import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from . import media
from .constants import THUMBNAIL_EDGE
from .media import get_cached_thumbnail, thumbnail_cache_path
from .models import MediaItem

# Leave cores free for the interactive thumbnail loader and the UI.
DEFAULT_WORKERS = max(1, (os.cpu_count() or 4) // 2)
PROGRESS_INTERVAL = 0.25


def _cached_names(thumbnail_dir: Path) -> set[str]:
    """Names of every file in the thumbnail cache. Listing the 256 fan-out folders once is
    far faster than checking each of tens of thousands of files on its own."""
    names: set[str] = set()
    try:
        with os.scandir(thumbnail_dir) as folders:
            for folder in folders:
                if folder.is_dir():
                    with os.scandir(folder.path) as entries:
                        names.update(entry.name for entry in entries)
    except FileNotFoundError:
        pass
    return names


@dataclass(frozen=True)
class WarmResult:
    total: int          # items checked
    missing: int        # items that needed a thumbnail
    created: int
    failed: int
    cancelled: bool


def missing_thumbnails(items: Sequence[MediaItem], edge: int = THUMBNAIL_EDGE,
                       stop_event: threading.Event | None = None) -> list[MediaItem]:
    """Items with neither a cached thumbnail (of this size) nor a remembered failure.

    Checking a large library takes about a second; if `stop_event` is set meanwhile, the
    result covers only the items checked so far.
    """
    names = _cached_names(media.THUMBNAIL_DIR)
    missing = []
    for index, item in enumerate(items):
        if index % 1000 == 0 and stop_event is not None and stop_event.is_set():
            break
        stem = thumbnail_cache_path(item.path, item.file_size, item.modified_ns, edge).stem
        if f"{stem}.jpg" not in names and f"{stem}.failed" not in names:
            missing.append(item)
    return missing


def warm_thumbnails(items: Sequence[MediaItem], stop_event: threading.Event,
                    on_progress: Callable[[int, int], None] | None = None,
                    workers: int = DEFAULT_WORKERS, edge: int = THUMBNAIL_EDGE) -> WarmResult:
    """Create thumbnails for `items` (in order) that don't have one yet.

    `on_progress(done, missing)` is called from this thread every so often and at the end.
    """
    todo = missing_thumbnails(items, edge, stop_event)
    created = failed = done = 0
    if not todo:
        return WarmResult(len(items), 0, 0, 0, stop_event.is_set())
    if on_progress:
        on_progress(0, len(todo))
    last_report = time.monotonic()
    workers = max(1, min(workers, len(todo)))
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="thumb-warm")
    in_flight: set[Future] = set()
    try:
        def collect(return_when: str) -> None:
            nonlocal created, failed, done, last_report
            finished, _ = wait(in_flight, return_when=return_when)
            for future in finished:
                in_flight.discard(future)
                done += 1
                try:
                    ok = future.result() is not None
                except Exception:
                    ok = False
                created += ok
                failed += not ok
            now = time.monotonic()
            if on_progress and now - last_report >= PROGRESS_INTERVAL:
                last_report = now
                on_progress(done, len(todo))

        for item in todo:
            if stop_event.is_set():
                break
            in_flight.add(executor.submit(get_cached_thumbnail, item.path, item.media_type,
                                          item.file_size, item.modified_ns, edge))
            if len(in_flight) >= workers * 2:
                collect(FIRST_COMPLETED)
        while in_flight and not stop_event.is_set():
            collect(FIRST_COMPLETED)
    finally:
        # On cancel, drop queued work; running jobs finish on their own and write valid files.
        executor.shutdown(wait=False, cancel_futures=True)
    cancelled = stop_event.is_set() and done < len(todo)
    if on_progress and not cancelled:
        on_progress(done, len(todo))
    return WarmResult(len(items), len(todo), created, failed, cancelled)
