"""
Small diagram widget showing page-number position as % from bottom-left.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget


class PositionDiagramWidget(QWidget):
    """Draws a page rectangle and a marker at (x%, y%) from the bottom-left."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._x_percent = 50.0
        self._y_percent = 5.0
        self.setMinimumSize(120, 150)
        self.setMaximumSize(160, 190)
        self.setToolTip(
            "Position is measured from the bottom-left corner of the page.\n"
            "X and Y are percentages of page width and height."
        )

    def set_position(self, x_percent: float, y_percent: float) -> None:
        self._x_percent = max(0.0, min(100.0, x_percent))
        self._y_percent = max(0.0, min(100.0, y_percent))
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        margin = 8
        w = self.width() - 2 * margin
        h = self.height() - 2 * margin - 14
        left = margin
        bottom = self.height() - margin - 12

        painter.setPen(QPen(QColor(80, 80, 80), 1))
        painter.setBrush(QColor(250, 250, 250))
        painter.drawRect(left, bottom - h, w, h)

        painter.setPen(QPen(QColor(120, 120, 120), 1))
        painter.drawLine(left, bottom, left + 14, bottom)
        painter.drawLine(left, bottom, left, bottom - 14)
        painter.drawText(left + 2, bottom + 11, "0,0")

        x = left + (self._x_percent / 100.0) * w
        y = bottom - (self._y_percent / 100.0) * h

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
