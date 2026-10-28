"""Create every missing thumbnail in the background, so scrolling never waits on an original.

Creating a thumbnail means decoding the full photo or a video frame (about 50 ms for a
12 MP JPEG, about 450 ms for a HEIC), while reading an existing one takes about 1 ms.
Doing the slow part ahead of time is what makes the grid feel instant.
"""

import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Callable, Sequence

from .media import _failure_marker, get_cached_thumbnail, thumbnail_cache_path
from .models import MediaItem

# Leave cores free for the interactive thumbnail loader and the UI.
DEFAULT_WORKERS = max(1, (os.cpu_count() or 4) // 2)
PROGRESS_INTERVAL = 0.25


@dataclass(frozen=True)
class WarmResult:
    total: int          # items checked
    missing: int        # items that needed a thumbnail
    created: int
    failed: int
    cancelled: bool


def missing_thumbnails(items: Sequence[MediaItem]) -> list[MediaItem]:
    """Items with neither a cached thumbnail nor a remembered failure."""
    missing = []
    for item in items:
        target = thumbnail_cache_path(item.path, item.file_size, item.modified_ns)
        if not target.exists() and not _failure_marker(target).exists():
            missing.append(item)
    return missing


def warm_thumbnails(items: Sequence[MediaItem], stop_event: threading.Event,
                    on_progress: Callable[[int, int], None] | None = None,
                    workers: int = DEFAULT_WORKERS) -> WarmResult:
    """Create thumbnails for `items` (in order) that don't have one yet.

    `on_progress(done, missing)` is called from this thread every so often and at the end.
    """
    todo = missing_thumbnails(items)
    created = failed = done = 0
    if not todo:
        return WarmResult(len(items), 0, 0, 0, stop_event.is_set())
    if on_progress:
        on_progress(0, len(todo))
    last_report = time.monotonic()
    executor = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="thumb-warm")
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
                                          item.file_size, item.modified_ns))
            if len(in_flight) >= max(1, workers) * 2:
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
