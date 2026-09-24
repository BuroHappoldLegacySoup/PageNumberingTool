"""
Compact form layout helpers for the main window.
"""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QAbstractSlider,
    QAbstractSpinBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QScrollBar,
    QSizePolicy,
    QSpinBox,
    QWidget,
)

# Comfortable minimums for legibility on high-DPI displays
FIELD_MIN_HEIGHT = 28


class WheelGuard(QObject):
    """
    Application-wide filter that stops the mouse wheel from changing spin boxes,
    combo boxes and sliders. The wheel event is passed on to the parent so the
    surrounding scroll area still scrolls.
    """

    def eventFilter(self, obj, event):  # noqa: N802 - Qt naming
        if event.type() == QEvent.Type.Wheel and _is_wheel_guarded(obj):
            event.ignore()
            return True
        return super().eventFilter(obj, event)


def _is_wheel_guarded(obj: QObject) -> bool:
    if isinstance(obj, (QAbstractSpinBox, QComboBox)):
        return True
    return isinstance(obj, QAbstractSlider) and not isinstance(obj, QScrollBar)


def prepare_line_edit(edit: QLineEdit, min_width: int) -> QLineEdit:
    edit.setMinimumHeight(FIELD_MIN_HEIGHT)
    edit.setMinimumWidth(min_width)
    edit.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
    return edit


def prepare_spinbox(spin: QSpinBox, min_width: int) -> QSpinBox:
    spin.setMinimumHeight(FIELD_MIN_HEIGHT)
    spin.setMinimumWidth(min_width)
    spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return spin


def prepare_double_spinbox(spin: QDoubleSpinBox, min_width: int) -> QDoubleSpinBox:
    spin.setMinimumHeight(FIELD_MIN_HEIGHT)
    spin.setMinimumWidth(min_width)
    spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return spin


def prepare_combo_box(combo: QComboBox, min_width: int) -> QComboBox:
    combo.setMinimumHeight(FIELD_MIN_HEIGHT)
    combo.setMinimumWidth(min_width)
    combo.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
    return combo


def add_form_row(
    grid: QGridLayout,
    row: int,
    label_text: str,
    field: QWidget,
    *,
    label_width: int = 130,
) -> None:
    """Place a right-aligned label beside a field widget."""
    label = QLabel(label_text)
    label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    label.setMinimumWidth(label_width)
    label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
    grid.addWidget(label, row, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    grid.addWidget(field, row, 1, 1, 2, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)


def configure_compact_grid(grid: QGridLayout) -> None:
    grid.setHorizontalSpacing(10)
    grid.setVerticalSpacing(8)
    grid.setColumnStretch(0, 0)
    grid.setColumnStretch(1, 1)
    grid.setColumnStretch(2, 0)
