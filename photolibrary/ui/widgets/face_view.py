"""A face cut out of a photo's grid thumbnail: for the sidebar's People and the Review page."""

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...core.models import MediaItem
from ..thumbnails import ThumbnailLoader

FACE_BUCKET = 512  # faces are small parts of a photo: cut them from the largest grid thumbnail


def face_source_rect(width: int, height: int, box, room: float = 1.35) -> QRectF:
    """The square of a `width` × `height` picture around the face `box` (fractions), with some room."""
    x, y, w, h = box[0] * width, box[1] * height, box[2] * width, box[3] * height
    side = min(max(w, h) * room, width, height)
    left = min(max(0.0, x + w / 2 - side / 2), width - side)
    top = min(max(0.0, y + h / 2 - side / 2), height - side)
    return QRectF(left, top, side, side)


class FaceView(QWidget):
    """Shows one face, round, loading its thumbnail in the background when needed."""

    def __init__(self, loader: ThumbnailLoader, theme, size: int = 200, parent=None):
        super().__init__(parent)
        self.loader = loader
        self.theme = theme
        self.item: MediaItem | None = None
        self.box = None
        self.setFixedSize(size, size)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        loader.updated.connect(self._thumbnail_ready)

    def set_face(self, item: MediaItem | None, box) -> None:
        self.item, self.box = item, box
        self.update()

    def _thumbnail_ready(self, item_id: int) -> None:
        if self.item is not None and self.item.id == item_id:
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
            path = QPainterPath()
            path.addEllipse(rect)
            painter.setClipPath(path)
            pixmap = None
            if self.item is not None:
                pixmap = self.loader.pixmap(self.item, FACE_BUCKET)
                if not self.loader.has_exact(self.item, FACE_BUCKET):
                    self.loader.request(self.item, FACE_BUCKET)
            if pixmap is not None and self.box:
                painter.drawPixmap(rect, pixmap, face_source_rect(pixmap.width(), pixmap.height(), self.box))
            else:
                painter.fillRect(rect, QColor(self.theme.colors.border))
        finally:
            painter.end()
