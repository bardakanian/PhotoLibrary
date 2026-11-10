"""Checkboxes drawn in the theme's colours.

Fusion draws an unticked box in the window's base colour, which in the dark theme is a black
square on a near-black background: invisible. This draws every checkbox (including ticks in
lists, like the people in a group) with a visible outline, and ticked ones in the accent colour.
"""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QProxyStyle, QStyle

from .colors import LIGHT, Palette

_CHECKS = {QStyle.PrimitiveElement.PE_IndicatorCheckBox, QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck}


class ThemedStyle(QProxyStyle):
    def __init__(self, base: str = "Fusion"):
        super().__init__(base)
        self.colors: Palette = LIGHT

    def drawPrimitive(self, element, option, painter, widget=None) -> None:
        if element not in _CHECKS:
            super().drawPrimitive(element, option, painter, widget)
            return
        colors = self.colors
        state = option.state
        ticked = bool(state & QStyle.StateFlag.State_On)
        partly = bool(state & QStyle.StateFlag.State_NoChange)
        enabled = bool(state & QStyle.StateFlag.State_Enabled)
        side = min(option.rect.width(), option.rect.height(), 16) - 2
        box = QRectF(0, 0, side, side)
        box.moveCenter(QRectF(option.rect).center())
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setOpacity(1.0 if enabled else 0.45)
            if ticked or partly:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(colors.accent))
                painter.drawRoundedRect(box, 3.5, 3.5)
                pen = QPen(QColor("#FFFFFF"), 1.8)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                if partly:
                    painter.drawLine(QPointF(box.left() + 4, box.center().y()), QPointF(box.right() - 4, box.center().y()))
                else:
                    painter.drawPolyline([QPointF(box.left() + side * 0.24, box.top() + side * 0.52),
                                          QPointF(box.left() + side * 0.43, box.top() + side * 0.71),
                                          QPointF(box.left() + side * 0.77, box.top() + side * 0.30)])
            else:
                painter.setPen(QPen(QColor(colors.text_subtle), 1.3))
                painter.setBrush(QColor(colors.surface))
                painter.drawRoundedRect(box.adjusted(0.65, 0.65, -0.65, -0.65), 3.5, 3.5)
            if state & QStyle.StateFlag.State_HasFocus and widget is not None and widget.inherits("QCheckBox"):
                painter.setPen(QPen(QColor(colors.accent), 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(box.adjusted(-2, -2, 2, 2), 5, 5)
        finally:
            painter.restore()
