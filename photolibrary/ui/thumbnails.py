"""Background thumbnail loading for the grid.

The UI thread only ever looks things up in an in-memory LRU of QPixmaps. Misses are
queued to worker threads: a fast pool that decodes thumbnails already on disk, and a
slower pool that creates them (PIL / ffmpeg). Queued work for cells that scrolled
away is dropped, so the pools always work on what is on screen first.
"""

import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Signal, Slot
from PySide6.QtGui import QImage, QImageReader, QPixmap

from ..core.cache import LRUCache
from ..core.constants import COVER_EDGE, HIGH_THUMBNAIL_EDGE, THUMBNAIL_EDGE
from ..core.media import get_cached_thumbnail, thumbnail_cache_path
from ..core.models import MediaItem
from ..core.workqueue import WorkQueue

# Decode sizes (device pixels of the cell's shorter side). Few buckets keep the cache
# effective across zoom levels; the painter scales the small remaining difference.
BUCKETS = (96, 160, 256, 384, 512)
# Thumbnail quality High adds bigger grid sizes (a 1,024 px thumbnail's shorter side is about 768 px).
# None equals a cover bucket: the bucket alone says which file a pixmap was decoded from.
HIGH_BUCKETS = BUCKETS + (640, 720)
# Years/Months tiles are much larger than grid cells; they decode from the 1,600 px covers.
COVER_BUCKETS = (768, 1024, 1536)
ALL_BUCKETS = HIGH_BUCKETS + COVER_BUCKETS  # every size a pixmap may be cached at
_grid_edge = THUMBNAIL_EDGE  # set from Settings ▸ Thumbnail quality


def set_high_quality(high: bool) -> None:
    """Grid thumbnails at 1,024 px (High) or 512 px (Standard). Call loader.reset() afterwards."""
    global _grid_edge
    _grid_edge = HIGH_THUMBNAIL_EDGE if high else THUMBNAIL_EDGE


def grid_edge() -> int:
    """The size grid thumbnails are made at (THUMBNAIL_EDGE or HIGH_THUMBNAIL_EDGE)."""
    return _grid_edge


def grid_buckets() -> tuple[int, ...]:
    return HIGH_BUCKETS if _grid_edge == HIGH_THUMBNAIL_EDGE else BUCKETS
CACHE_BYTES = 480 * 1024 * 1024
# A thumbnail that couldn't be made is tried again after this long: the file may have been on a
# drive that was asleep or unplugged. (Files that really can't be read have a .failed marker on
# disk, so trying again costs one file check.)
FAILED_RETRY_SECONDS = 30.0


def bucket_for(pixels: float, cover: bool = False) -> int:
    """Smallest decode size covering `pixels`. Only covers may go above the grid's 512 px."""
    sizes = grid_buckets() + COVER_BUCKETS if cover else grid_buckets()
    for bucket in sizes:
        if bucket >= pixels:
            return bucket
    return sizes[-1]


def edge_for_bucket(bucket: int) -> int:
    return COVER_EDGE if bucket in COVER_BUCKETS else _grid_edge


def _other_grid_edge(edge: int) -> int | None:
    """The grid size of the other quality: it stands in while this one is being made."""
    return {THUMBNAIL_EDGE: HIGH_THUMBNAIL_EDGE, HIGH_THUMBNAIL_EDGE: THUMBNAIL_EDGE}.get(edge)


def decode_scaled(path: Path, bucket: int) -> QImage | None:
    """Decode a cached thumbnail so its shorter side is about `bucket` pixels. Thread-safe."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and min(size.width(), size.height()) > bucket:
        scale = bucket / min(size.width(), size.height())
        reader.setScaledSize(QSize(max(1, round(size.width() * scale)), max(1, round(size.height() * scale))))
    image = reader.read()
    if image.isNull():
        return None
    # A premultiplied format is the fastest for QPixmap conversion and painting.
    return image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def _thumb_key(item: MediaItem, bucket: int) -> tuple:
    return (item.id, item.modified_ns, bucket)


class _Signals(QObject):
    loaded = Signal(object, object, int, bool)  # key, QImage | None, generation, final (not a stand-in)


class ThumbnailLoader(QObject):
    """Owns the pixmap cache. Emits `updated(item_id)` when a thumbnail becomes available."""

    updated = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cache: LRUCache[QPixmap] = LRUCache(CACHE_BYTES)
        self._failed: dict[tuple, float] = {}  # (id, modified_ns) -> when its thumbnail last failed
        self._requested: set[tuple] = set()   # keys queued or running, owned by the UI thread
        # Keys showing the other quality's thumbnail while theirs is made (after switching quality).
        self._interim: set[tuple] = set()
        self._generation = 0
        self._signals = _Signals()
        self._signals.loaded.connect(self._on_loaded)
        cpus = os.cpu_count() or 4
        self._read_queue = WorkQueue(4, "thumb-read")
        self._generate_queue = WorkQueue(max(2, min(6, cpus - 1)), "thumb-generate")

    # UI-thread API -------------------------------------------------------

    def pixmap(self, item: MediaItem, bucket: int) -> QPixmap | None:
        """Best cached pixmap for an item: the requested size, else any other size."""
        exact = self.cache.get(_thumb_key(item, bucket))
        if exact is not None:
            return exact
        for other in ALL_BUCKETS:
            if other != bucket:
                fallback = self.cache.get(_thumb_key(item, other))
                if fallback is not None:
                    return fallback
        return None

    def has_exact(self, item: MediaItem, bucket: int) -> bool:
        key = _thumb_key(item, bucket)
        return key in self.cache and key not in self._interim

    def is_unavailable(self, item: MediaItem) -> bool:
        failed_at = self._failed.get((item.id, item.modified_ns))
        if failed_at is None:
            return False
        if time.monotonic() - failed_at < FAILED_RETRY_SECONDS:
            return True
        del self._failed[(item.id, item.modified_ns)]  # worth another try
        return False

    def request(self, item: MediaItem, bucket: int) -> None:
        key = _thumb_key(item, bucket)
        if key in self._requested or (key in self.cache and key not in self._interim) or self.is_unavailable(item):
            return
        self._requested.add(key)
        generation, edge = self._generation, edge_for_bucket(bucket)  # read on this thread
        self._read_queue.submit(key, lambda: self._read_job(item, bucket, key, generation, edge))

    def focus(self, items: list[MediaItem], bucket: int) -> None:
        """Keep only queued work for `items` (in order), then request any that are missing."""
        keys = [_thumb_key(item, bucket) for item in items]
        for dropped in self._read_queue.retain(keys) + self._generate_queue.retain(keys):
            self._requested.discard(dropped)
        for item in items:
            self.request(item, bucket)

    def forget(self, item_ids: set[int]) -> None:
        self.cache.discard_where(lambda key: key[0] in item_ids)

    def reset(self) -> None:
        """Drop all queued work and cached pixmaps (for example after changing folders)."""
        self._generation += 1
        self._read_queue.clear()
        self._generate_queue.clear()
        self._requested.clear()
        self._interim.clear()
        self._failed.clear()
        self.cache.clear()

    def shutdown(self) -> None:
        self._read_queue.shutdown()
        self._generate_queue.shutdown()

    @Slot(object, object, int, bool)
    def _on_loaded(self, key: tuple, image: QImage | None, generation: int, final: bool = True) -> None:
        if generation != self._generation:
            return
        if final:
            self._requested.discard(key)
            self._interim.discard(key)
        elif image is not None:
            self._interim.add(key)  # still requested: the real one follows
        item_id, modified_ns, _bucket = key
        if image is None and not final:
            return
        if image is None:
            self._failed[(item_id, modified_ns)] = time.monotonic()
        else:
            pixmap = QPixmap.fromImage(image)
            self.cache.put(key, pixmap, pixmap.width() * pixmap.height() * 4)
        self.updated.emit(item_id)

    # Worker-thread jobs --------------------------------------------------

    def _emit(self, key: tuple, image: QImage | None, generation: int, final: bool = True) -> None:
        try:
            self._signals.loaded.emit(key, image, generation, final)
        except RuntimeError:
            pass  # the loader was destroyed during shutdown

    def _read_job(self, item: MediaItem, bucket: int, key: tuple, generation: int, edge: int) -> None:
        cached = thumbnail_cache_path(item.path, item.file_size, item.modified_ns, edge)
        image = decode_scaled(cached, bucket) if cached.exists() else None
        if image is not None:
            self._emit(key, image, generation)
            return
        if cached.exists():
            # Damaged (say, cut short by a full disk): make it again rather than fail for good.
            try:
                cached.unlink()
            except OSError:
                pass
        # Just switched quality: the other size, already on disk, shows until this one is made.
        other = _other_grid_edge(edge)
        if other is not None:
            stand_in = thumbnail_cache_path(item.path, item.file_size, item.modified_ns, other)
            image = decode_scaled(stand_in, bucket) if stand_in.exists() else None
            if image is not None:
                self._emit(key, image, generation, final=False)
        # Not on disk yet: hand over to the slower pool that creates it.
        self._generate_queue.submit(key, lambda: self._generate_job(item, bucket, key, generation, edge))

    def _generate_job(self, item: MediaItem, bucket: int, key: tuple, generation: int, edge: int) -> None:
        result = get_cached_thumbnail(item.path, item.media_type, item.file_size, item.modified_ns, edge)
        self._emit(key, decode_scaled(result, bucket) if result else None, generation)
