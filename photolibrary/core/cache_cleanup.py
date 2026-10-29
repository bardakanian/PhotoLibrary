"""Remove thumbnails that no indexed file needs any more.

A thumbnail's file name is a hash of the source path, size, modification time and size class,
so editing, moving or deleting a photo leaves its old thumbnail behind. Without cleanup the
cache only ever grows.
"""

import hashlib
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .constants import COVER_EDGE, THUMBNAIL_EDGE

# Temporary files from interrupted thumbnail writes are removed once they are this old.
STALE_PART_SECONDS = 3600


@dataclass(frozen=True)
class PruneResult:
    kept: int
    removed: int
    freed_bytes: int


def _digest(path: str, file_size: int, modified_ns: int, edge: int) -> str:
    # Must match media.thumbnail_cache_path.
    return hashlib.sha256(f"{Path(path)}|{file_size}|{modified_ns}|{edge}".encode()).hexdigest()


def expected_names(entries: Iterable[tuple[str, int, int]],
                   stop_event: threading.Event | None = None) -> set[str] | None:
    """Thumbnail hashes (grid and cover) for (file_path, file_size, modified_ns) entries.

    None if `stop_event` was set before every entry was hashed: a partial set must never be
    used to decide what to delete.
    """
    names = set()
    for index, (path, size, modified) in enumerate(entries):
        if index % 1000 == 0 and stop_event is not None and stop_event.is_set():
            return None
        names.update(_digest(path, size, modified, edge) for edge in (THUMBNAIL_EDGE, COVER_EDGE))
    return names


def prune_thumbnail_cache(thumbnail_dir: Path, entries: Iterable[tuple[str, int, int]],
                          now: float | None = None, stop_event: threading.Event | None = None) -> PruneResult:
    """Delete cache files not belonging to `entries` (every row in the index, all folders).

    Refuses to run with no entries, so an empty or unreadable index never wipes the cache.
    If `stop_event` is set, stops between cache folders (or before deleting anything).
    """
    keep = expected_names(entries, stop_event)
    if not keep or not thumbnail_dir.is_dir():
        return PruneResult(0, 0, 0)
    now = time.time() if now is None else now
    kept = removed = freed = 0
    for bucket in thumbnail_dir.iterdir():
        if stop_event is not None and stop_event.is_set():
            break
        if not bucket.is_dir():
            continue
        for cached in bucket.iterdir():
            name = cached.name
            try:
                stat = cached.stat()
                if ".part" in name:
                    stale = now - stat.st_mtime > STALE_PART_SECONDS
                else:
                    stale = name.split(".", 1)[0] not in keep
                if stale:
                    cached.unlink()
                    removed += 1
                    freed += stat.st_size
                else:
                    kept += 1
            except OSError:
                continue
        try:
            bucket.rmdir()  # only succeeds when the folder is now empty
        except OSError:
            pass
    return PruneResult(kept, removed, freed)
