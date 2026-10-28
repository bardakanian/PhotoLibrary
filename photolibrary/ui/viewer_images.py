"""Background decoding of full-size photos for the viewer, with a small cache for neighbours."""

from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Signal, Slot
from PySide6.QtGui import QImage, QImageReader

from ..core.media import load_display_image
from ..core.models import MediaItem
from ..core.workqueue import WorkQueue

CACHE_SIZE = 5  # current photo, its neighbours, and a little history


def decode_display_image(path: Path, max_edge: int | None) -> QImage | None:
    """EXIF-rotated image, at most `max_edge` px on its longest side (None: full size). Thread-safe."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if max_edge and size.isValid() and max(size.width(), size.height()) > max_edge:
        # For rotated photos size() is pre-rotation, but the longest side is the same either way.
        scale = max_edge / max(size.width(), size.height())
        reader.setScaledSize(QSize(max(1, round(size.width() * scale)), max(1, round(size.height() * scale))))
    image = reader.read()
    if not image.isNull():
        return image
    # Formats Qt can't read here (or damaged files Pillow tolerates) go through Pillow.
    try:
        pil_image = load_display_image(path, max_edge or 1 << 16)
    except Exception:
        return None
    mode = pil_image.mode
    if mode not in {"RGB", "RGBA"}:
        pil_image = pil_image.convert("RGB")
        mode = "RGB"
    data = pil_image.tobytes()
    image_format = QImage.Format.Format_RGBA8888 if mode == "RGBA" else QImage.Format.Format_RGB888
    channels = 4 if mode == "RGBA" else 3
    return QImage(data, pil_image.width, pil_image.height, pil_image.width * channels, image_format).copy()


class _Signals(QObject):
    loaded = Signal(object, object)  # key, QImage | None


class ViewerImageLoader(QObject):
    """Emits `loaded(item_id, image, full_resolution)` on the UI thread."""

    loaded = Signal(int, object, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue = WorkQueue(2, "viewer-decode")
        self._cache: OrderedDict[tuple, QImage | None] = OrderedDict()
        self._signals = _Signals()
        self._signals.loaded.connect(self._on_loaded)

    def cached(self, item: MediaItem, max_edge: int | None) -> QImage | None:
        image = self._cache.get(self._key(item, max_edge))
        if image is None and max_edge is not None:
            # A full-resolution decode is just as good.
            image = self._cache.get(self._key(item, None))
        return image

    def request(self, item: MediaItem, max_edge: int | None) -> None:
        key = self._key(item, max_edge)
        if key in self._cache:
            self.loaded.emit(item.id, self._cache[key], max_edge is None)
            return
        path = item.path

        def job():
            image = decode_display_image(path, max_edge)
            try:
                self._signals.loaded.emit(key, image)
            except RuntimeError:
                pass

        self._queue.submit(key, job)

    def focus(self, keep: list[tuple[MediaItem, int | None]]) -> None:
        """Drop queued decodes that are not for the current photo or its neighbours."""
        self._queue.retain([self._key(item, edge) for item, edge in keep])

    def shutdown(self) -> None:
        self._queue.shutdown()

    @staticmethod
    def _key(item: MediaItem, max_edge: int | None) -> tuple:
        return (item.id, item.modified_ns, max_edge)

    @Slot(object, object)
    def _on_loaded(self, key: tuple, image: QImage | None) -> None:
        self._cache[key] = image
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        self.loaded.emit(key[0], image, key[2] is None)
