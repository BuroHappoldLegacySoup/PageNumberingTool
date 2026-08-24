"""
Small diagram widget showing page-number position as % from a chosen origin corner.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from backend.page_number_config import (
    DEFAULT_ORIGIN,
    ORIGIN_BOTTOM_LEFT,
    ORIGIN_BOTTOM_RIGHT,
    ORIGIN_TOP_LEFT,
    ORIGIN_TOP_RIGHT,
    origin_percent_to_bottom_left_percent,
)

_ORIGIN_LABELS = {
    ORIGIN_BOTTOM_LEFT: "bottom-left",
    ORIGIN_BOTTOM_RIGHT: "bottom-right",
    ORIGIN_TOP_LEFT: "top-left",
    ORIGIN_TOP_RIGHT: "top-right",
}


class PositionDiagramWidget(QWidget):
    """Draws a page rectangle and a marker at (x%, y%) from the active origin."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._x_percent = 50.0
        self._y_percent = 5.0
        self._origin = DEFAULT_ORIGIN
        self._aspect_ratio = 210.0 / 297.0
        self.setMinimumSize(120, 150)
        self.setMaximumSize(160, 190)
        self._update_tooltip()

    def _update_tooltip(self) -> None:
        corner = _ORIGIN_LABELS.get(self._origin, "bottom-left")
        self.setToolTip(
            f"Position is measured from the {corner} corner of the page.\n"
            "X and Y are percentages of page width and height, inward from that corner."
        )

    def set_origin(self, origin: str) -> None:
        self._origin = origin if origin in _ORIGIN_LABELS else DEFAULT_ORIGIN
        self._update_tooltip()
        self.update()

    def set_position(self, x_percent: float, y_percent: float) -> None:
        self._x_percent = max(0.0, min(100.0, x_percent))
        self._y_percent = max(0.0, min(100.0, y_percent))
        self.update()

    def set_page_size(self, width: float, height: float) -> None:
        """Shape the drawn page to a real width/height, so landscape looks landscape."""
        if width <= 0 or height <= 0:
            return
        self._aspect_ratio = width / height
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        margin = 8
        available_w = self.width() - 2 * margin
        available_h = self.height() - 2 * margin - 14

        # Fit the page rectangle to its real aspect ratio, centred horizontally.
        w = available_w
        h = int(round(w / self._aspect_ratio)) if self._aspect_ratio > 0 else available_h
        if h > available_h:
            h = available_h
            w = int(round(h * self._aspect_ratio))
        left = margin + (available_w - w) // 2
        top = margin + 12 + (available_h - h) // 2
        bottom = top + h
        right = left + w

        painter.setPen(QPen(QColor(80, 80, 80), 1))
        painter.setBrush(QColor(250, 250, 250))
        painter.drawRect(left, top, w, h)

        # Origin marker and axes at the active corner.
        painter.setPen(QPen(QColor(120, 120, 120), 1))
        axis = 14
        if self._origin == ORIGIN_BOTTOM_RIGHT:
            ox, oy = right, bottom
            painter.drawLine(ox, oy, ox - axis, oy)
            painter.drawLine(ox, oy, ox, oy - axis)
            painter.drawText(ox - 22, oy + 11, "0,0")
        elif self._origin == ORIGIN_TOP_LEFT:
            ox, oy = left, top
            painter.drawLine(ox, oy, ox + axis, oy)
            painter.drawLine(ox, oy, ox, oy + axis)
            painter.drawText(ox + 2, oy - 2, "0,0")
        elif self._origin == ORIGIN_TOP_RIGHT:
            ox, oy = right, top
            painter.drawLine(ox, oy, ox - axis, oy)
            painter.drawLine(ox, oy, ox, oy + axis)
            painter.drawText(ox - 22, oy - 2, "0,0")
        else:
            ox, oy = left, bottom
            painter.drawLine(ox, oy, ox + axis, oy)
            painter.drawLine(ox, oy, ox, oy - axis)
            painter.drawText(ox + 2, oy + 11, "0,0")

        bl_x, bl_y = origin_percent_to_bottom_left_percent(
            self._x_percent, self._y_percent, self._origin
        )
        x = left + (bl_x / 100.0) * w
        y = bottom - (bl_y / 100.0) * h

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(206, 220, 0))
        painter.drawEllipse(int(x) - 5, int(y) - 5, 10, 10)

        painter.setPen(QColor(60, 60, 60))
        painter.drawText(
            left,
            margin,
            f"X: {self._x_percent:.0f}%  Y: {self._y_percent:.0f}%",
        )

        painter.end()
