from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from ...core.zoom import ZoomState

BACKGROUND = QColor("#0B0C0E")


class ImageCanvas(QWidget):
    """Shows one photo fitted to the window, with zoom (wheel, pinch, double-click) and drag-to-pan.

    The natural size is the photo's real pixel size. The pixmap may be smaller (a thumbnail or a
    screen-sized decode); it is always drawn at the natural size's geometry, so a sharper
    pixmap can replace it without the picture moving.
    """

    zoom_changed = Signal(float)          # scale in device pixels per image pixel (1.0 = actual size)
    needs_full_resolution = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self._pixmap: QPixmap | None = None
        self._natural: tuple[int, int] | None = None
        self._natural_from_pixmap = False
        self._state: ZoomState | None = None
        self._drag_origin: QPointF | None = None
        self._message = ""
        self._message_font = QFont()
        self._message_font.setPixelSize(15)

    # Content ---------------------------------------------------------------

    def set_content(self, pixmap: QPixmap | None, natural: tuple[int, int] | None) -> None:
        """Show a new photo, fitted. `natural` is its full pixel size if known."""
        self._message = ""
        self._pixmap = pixmap
        self._natural_from_pixmap = not natural
        self._natural = natural or (self._pixmap_size(pixmap) if pixmap else None)
        self._state = ZoomState.fitted(*self._natural, self.width(), self.height()) if self._natural else None
        self._emit_zoom()
        self.update()

    def set_pixmap(self, pixmap: QPixmap) -> None:
        """Swap in a sharper pixmap for the same photo, keeping the current zoom and position."""
        self._message = ""
        size = self._pixmap_size(pixmap)
        if self._natural is None or (self._natural_from_pixmap and size[0] > self._natural[0]):
            # Without a known natural size, the largest decode so far stands in for it.
            fitted = self._state is None or self._state.is_fitted
            self._natural = size
            self._state = ZoomState.fitted(*size, self.width(), self.height()) if fitted or self._state is None \
                else ZoomState.fitted(*size, self.width(), self.height()).with_scale(self._state.scale)
        elif self._state is None:
            # A message ("Loading…") was shown meanwhile; without a state nothing would be drawn.
            self._state = ZoomState.fitted(*self._natural, self.width(), self.height())
        self._pixmap = pixmap
        self._emit_zoom()
        self.update()

    def set_message(self, text: str) -> None:
        self._message = text
        self._pixmap = None
        self._state = None
        self.update()

    def clear(self) -> None:
        self._pixmap = None
        self._state = None
        self._natural = None
        self._message = ""
        self.update()

    @staticmethod
    def _pixmap_size(pixmap: QPixmap) -> tuple[int, int]:
        return pixmap.width(), pixmap.height()

    # Zoom API --------------------------------------------------------------

    @property
    def has_image(self) -> bool:
        return self._pixmap is not None

    @property
    def is_fitted(self) -> bool:
        return self._state is None or self._state.is_fitted

    @property
    def actual_scale(self) -> float:
        """Scale in view units at which one image pixel covers one device pixel."""
        return 1 / self.devicePixelRatioF()

    def zoom_by(self, factor: float, anchor: QPointF | None = None) -> None:
        if self._state is None:
            return
        point = (anchor.x(), anchor.y()) if anchor is not None else None
        self._set_state(self._state.with_scale(self._state.scale * factor, point))

    def zoom_in(self) -> None:
        self.zoom_by(1.25)

    def zoom_out(self) -> None:
        self.zoom_by(1 / 1.25)

    def zoom_to_fit(self) -> None:
        if self._state is not None:
            self._set_state(self._state.with_scale(self._state.fit_scale))

    def toggle_actual_size(self, anchor: QPointF | None = None) -> None:
        """Fit <-> 100% (actual pixels), or 2x for photos that already fit at 100%."""
        if self._state is None:
            return
        if self._state.is_fitted:
            target = max(self.actual_scale, self._state.fit_scale * 2)
            point = (anchor.x(), anchor.y()) if anchor is not None else None
            self._set_state(self._state.with_scale(target, point))
        else:
            self.zoom_to_fit()

    def _set_state(self, state: ZoomState) -> None:
        self._state = state
        self._emit_zoom()
        self._update_cursor()
        self.update()

    def _emit_zoom(self) -> None:
        if self._state is None:
            return
        self.zoom_changed.emit(self._state.scale * self.devicePixelRatioF())
        # Ask for a sharper decode when the current pixmap is being stretched.
        if self._pixmap is not None and self._natural is not None:
            needed = self._natural[0] * self._state.scale * self.devicePixelRatioF()
            if self._pixmap.width() < min(needed * 0.95, self._natural[0] * 0.99):
                self.needs_full_resolution.emit()

    def _update_cursor(self) -> None:
        if self._state is None or self._state.is_fitted:
            self.unsetCursor()
        else:
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor if self._drag_origin else Qt.CursorShape.OpenHandCursor))

    # Events ----------------------------------------------------------------

    def resizeEvent(self, event) -> None:
        if self._state is not None:
            self._state = self._state.resized(self.width(), self.height())
            self._emit_zoom()
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:  # always end it; see MediaGridView.paintEvent
            painter.fillRect(self.rect(), BACKGROUND)
            if self._pixmap is not None and self._state is not None:
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                x, y, width, height = self._state.target_rect()
                painter.drawPixmap(QRectF(x, y, width, height), self._pixmap, QRectF(self._pixmap.rect()))
            elif self._message:
                painter.setPen(QColor("#A6ADBA"))
                painter.setFont(self._message_font)
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
        finally:
            painter.end()

    def wheelEvent(self, event) -> None:
        if self._state is None:
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_by(1.0015 ** event.angleDelta().y(), event.position())
        elif not self._state.is_fitted:
            delta = event.pixelDelta() if not event.pixelDelta().isNull() else event.angleDelta() / 2
            self._set_state(self._state.panned(delta.x(), delta.y()))
        event.accept()

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.NativeGesture and self._state is not None:
            gesture = event.gestureType()
            if gesture == Qt.NativeGestureType.ZoomNativeGesture:
                self.zoom_by(1 + event.value(), event.position())
                return True
            if gesture == Qt.NativeGestureType.SmartZoomNativeGesture:
                self.toggle_actual_size(event.position())
                return True
        return super().event(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_actual_size(event.position())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self.is_fitted:
            self._drag_origin = event.position()
            self._update_cursor()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_origin is not None and self._state is not None:
            delta = event.position() - self._drag_origin
            self._drag_origin = event.position()
            self._set_state(self._state.panned(delta.x(), delta.y()))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_origin = None
        self._update_cursor()
        super().mouseReleaseEvent(event)
