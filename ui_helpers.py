"""
Compact form layout helpers for the main window.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QSpinBox,
    QWidget,
)

# Comfortable minimums for legibility on high-DPI displays
FIELD_MIN_HEIGHT = 28


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
