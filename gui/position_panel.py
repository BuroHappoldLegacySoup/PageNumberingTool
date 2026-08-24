"""
Reusable page-number position controls for a single page type.

One panel collects relative (%) or absolute (cm) placement for pages of one
size/orientation, so a report mixing e.g. A4 Portrait and A3 Landscape can be
stamped differently on each.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from backend.page_number_config import (
    CUSTOM_POSITION,
    DEFAULT_ORIGIN,
    DEFAULT_POSITION,
    ORIGIN_BOTTOM_LEFT,
    ORIGIN_BOTTOM_RIGHT,
    ORIGIN_TOP_LEFT,
    ORIGIN_TOP_RIGHT,
    POSITION_ABSOLUTE,
    POSITION_PRESETS,
    POSITION_RELATIVE,
    preset_anchor,
)
from gui.position_diagram import PositionDiagramWidget
from gui.ui_helpers import prepare_combo_box, prepare_double_spinbox

A4_WIDTH_CM = 21.0
A4_HEIGHT_CM = 29.7


@dataclass
class PositionValues:
    """Position fields collected from one panel."""

    position_mode: str
    position_name: str
    position_origin: str
    x_percent: float
    y_percent: float
    x_cm: float
    y_cm: float
    text_anchor: str


class PositionSettingsPanel(QWidget):
    """
    Relative/absolute position controls plus a live diagram for one page type.

    Percentage and centimetre values are kept in sync using the panel's page
    dimensions, so absolute offsets mean the same thing on an A3 page as the
    preview shows.
    """

    changed = pyqtSignal()

    def __init__(
        self,
        page_width_cm: float = A4_WIDTH_CM,
        page_height_cm: float = A4_HEIGHT_CM,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._page_width_cm = page_width_cm if page_width_cm > 0 else A4_WIDTH_CM
        self._page_height_cm = page_height_cm if page_height_cm > 0 else A4_HEIGHT_CM
        self._position_origin: str = DEFAULT_ORIGIN
        self._sync_from_preset = False

        self._build_ui()
        self.apply_position_preset(DEFAULT_POSITION)
        self.position_diagram.set_page_size(self._page_width_cm, self._page_height_cm)

    # ------------------------------------------------------------------ build

    def _build_ui(self) -> None:
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        self.relative_position_radio = QRadioButton("Relative (%)")
        self.absolute_position_radio = QRadioButton("Absolute (cm)")
        self.relative_position_radio.setChecked(True)
        self._position_mode_group = QButtonGroup(self)
        self._position_mode_group.addButton(self.relative_position_radio)
        self._position_mode_group.addButton(self.absolute_position_radio)
        self.relative_position_radio.toggled.connect(self._on_position_mode_changed)
        row.addWidget(self.relative_position_radio)
        row.addWidget(self.absolute_position_radio)

        controls = QVBoxLayout()
        controls.setSpacing(6)

        self.page_size_label = QLabel("")
        self.page_size_label.setStyleSheet("color: #555; font-size: 11px;")
        self.page_size_label.hide()
        controls.addWidget(self.page_size_label)

        self.position_combo = prepare_combo_box(QComboBox(), 160)
        self.position_combo.addItems(list(POSITION_PRESETS.keys()) + [CUSTOM_POSITION])
        self.position_combo.setCurrentText(DEFAULT_POSITION)
        self.position_combo.currentTextChanged.connect(self._on_position_preset_changed)
        controls.addWidget(self.position_combo)

        slider_col = QVBoxLayout()
        slider_col.setSpacing(4)

        x_row = QHBoxLayout()
        x_row.setSpacing(6)
        x_row.addWidget(QLabel("X %:"))
        self.x_slider = QSlider(Qt.Orientation.Horizontal)
        self.x_slider.setRange(0, 100)
        self.x_slider.setValue(50)
        self.x_slider.setMinimumWidth(160)
        self.x_slider.valueChanged.connect(self._on_position_slider_changed)
        self.x_value_label = QLabel("50")
        self.x_value_label.setMinimumWidth(28)
        x_row.addWidget(self.x_slider, stretch=1)
        x_row.addWidget(self.x_value_label)
        slider_col.addLayout(x_row)

        y_row = QHBoxLayout()
        y_row.setSpacing(6)
        y_row.addWidget(QLabel("Y %:"))
        self.y_slider = QSlider(Qt.Orientation.Horizontal)
        self.y_slider.setRange(0, 100)
        self.y_slider.setValue(5)
        self.y_slider.setMinimumWidth(160)
        self.y_slider.valueChanged.connect(self._on_position_slider_changed)
        self.y_value_label = QLabel("5")
        self.y_value_label.setMinimumWidth(28)
        y_row.addWidget(self.y_slider, stretch=1)
        y_row.addWidget(self.y_value_label)
        slider_col.addLayout(y_row)

        abs_col = QVBoxLayout()
        abs_col.setSpacing(4)

        abs_x_row = QHBoxLayout()
        abs_x_row.setSpacing(6)
        abs_x_row.addWidget(QLabel("X (cm):"))
        self.x_cm_spinbox = prepare_double_spinbox(QDoubleSpinBox(), 88)
        self.x_cm_spinbox.setDecimals(2)
        self.x_cm_spinbox.setMinimum(0.0)
        self.x_cm_spinbox.setMaximum(999.99)
        self.x_cm_spinbox.setValue(self._page_width_cm * 0.5)
        self.x_cm_spinbox.valueChanged.connect(self._on_absolute_position_changed)
        abs_x_row.addWidget(self.x_cm_spinbox)
        abs_col.addLayout(abs_x_row)

        abs_y_row = QHBoxLayout()
        abs_y_row.setSpacing(6)
        abs_y_row.addWidget(QLabel("Y (cm):"))
        self.y_cm_spinbox = prepare_double_spinbox(QDoubleSpinBox(), 88)
        self.y_cm_spinbox.setDecimals(2)
        self.y_cm_spinbox.setMinimum(0.0)
        self.y_cm_spinbox.setMaximum(999.99)
        self.y_cm_spinbox.setValue(self._page_height_cm * 0.05)
        self.y_cm_spinbox.valueChanged.connect(self._on_absolute_position_changed)
        abs_y_row.addWidget(self.y_cm_spinbox)
        abs_col.addLayout(abs_y_row)

        self._relative_position_widget = QWidget()
        self._relative_position_widget.setLayout(slider_col)
        self._absolute_position_widget = QWidget()
        self._absolute_position_widget.setLayout(abs_col)
        controls.addWidget(self._relative_position_widget)
        controls.addWidget(self._absolute_position_widget)
        self._absolute_position_widget.hide()
        row.addLayout(controls, stretch=1)

        self.position_diagram = PositionDiagramWidget()
        row.addWidget(self.position_diagram)

    # ------------------------------------------------------------- page setup

    def set_page_size_cm(self, width_cm: float, height_cm: float) -> None:
        """
        Set the page dimensions used for cm/% conversion and the diagram shape.

        In absolute mode the user's centimetre offsets are authoritative and the
        percentage readout is recomputed; in relative mode the percentages are
        kept and the centimetre equivalents follow.
        """
        if width_cm <= 0 or height_cm <= 0:
            return
        self._page_width_cm = width_cm
        self._page_height_cm = height_cm
        self.position_diagram.set_page_size(width_cm, height_cm)

        self._sync_from_preset = True
        if self.absolute_position_radio.isChecked():
            x_pct, y_pct = self._cm_to_percent(
                self.x_cm_spinbox.value(), self.y_cm_spinbox.value()
            )
            self.x_slider.setValue(int(round(max(0.0, min(100.0, x_pct)))))
            self.y_slider.setValue(int(round(max(0.0, min(100.0, y_pct)))))
        else:
            x_cm, y_cm = self._percent_to_cm(
                float(self.x_slider.value()), float(self.y_slider.value())
            )
            self.x_cm_spinbox.setValue(x_cm)
            self.y_cm_spinbox.setValue(y_cm)
        self._sync_from_preset = False

        self._update_position_labels()
        self._sync_diagram()

    def set_page_size_caption(self, caption: str) -> None:
        """Show a size caption above the controls; an empty caption hides it."""
        self.page_size_label.setText(caption)
        self.page_size_label.setVisible(bool(caption))

    @property
    def page_width_cm(self) -> float:
        return self._page_width_cm

    @property
    def page_height_cm(self) -> float:
        return self._page_height_cm

    @property
    def position_origin(self) -> str:
        return self._position_origin

    # -------------------------------------------------------------- conversion

    def _percent_to_cm(self, x_pct: float, y_pct: float) -> Tuple[float, float]:
        return (
            x_pct / 100.0 * self._page_width_cm,
            y_pct / 100.0 * self._page_height_cm,
        )

    def _cm_to_percent(self, x_cm: float, y_cm: float) -> Tuple[float, float]:
        return (
            x_cm / self._page_width_cm * 100.0,
            y_cm / self._page_height_cm * 100.0,
        )

    def infer_text_anchor(
        self, x_percent: float, origin: Optional[str] = None
    ) -> str:
        """Infer left/center/right text anchor from X offset and origin corner."""
        active_origin = origin if origin is not None else self._position_origin
        from_right = active_origin in (ORIGIN_BOTTOM_RIGHT, ORIGIN_TOP_RIGHT)
        if from_right:
            if x_percent <= 20.0:
                return "right"
            if x_percent >= 80.0:
                return "left"
            return "center"
        if x_percent <= 20.0:
            return "left"
        if x_percent >= 80.0:
            return "right"
        return "center"

    # ----------------------------------------------------------------- handlers

    def _on_position_mode_changed(self) -> None:
        relative = self.relative_position_radio.isChecked()
        self._relative_position_widget.setVisible(relative)
        self._absolute_position_widget.setVisible(not relative)
        self.position_combo.setEnabled(relative)
        self.x_slider.setEnabled(relative)
        self.y_slider.setEnabled(relative)
        self._sync_diagram()
        self.changed.emit()

    def _on_absolute_position_changed(self) -> None:
        if not self._sync_from_preset:
            x_pct, y_pct = self._cm_to_percent(
                self.x_cm_spinbox.value(), self.y_cm_spinbox.value()
            )
            self.x_slider.blockSignals(True)
            self.y_slider.blockSignals(True)
            self.x_slider.setValue(int(round(max(0.0, min(100.0, x_pct)))))
            self.y_slider.setValue(int(round(max(0.0, min(100.0, y_pct)))))
            self.x_slider.blockSignals(False)
            self.y_slider.blockSignals(False)
            self._update_position_labels()
            self._set_combo_to_custom()
        self._sync_diagram_from_absolute()
        self.changed.emit()

    def _on_position_preset_changed(self, name: str) -> None:
        if name != CUSTOM_POSITION and name in POSITION_PRESETS:
            self._sync_from_preset = True
            x_pct, y_pct, _, origin = POSITION_PRESETS[name]
            self.set_position_origin(origin)
            self.x_slider.setValue(int(round(x_pct)))
            self.y_slider.setValue(int(round(y_pct)))
            x_cm, y_cm = self._percent_to_cm(x_pct, y_pct)
            self.x_cm_spinbox.setValue(x_cm)
            self.y_cm_spinbox.setValue(y_cm)
            self._sync_from_preset = False
            self._update_position_labels()
            self._sync_diagram()
        self.changed.emit()

    def _on_position_slider_changed(self) -> None:
        self._update_position_labels()
        if not self._sync_from_preset:
            x_cm, y_cm = self._percent_to_cm(
                float(self.x_slider.value()), float(self.y_slider.value())
            )
            self.x_cm_spinbox.blockSignals(True)
            self.y_cm_spinbox.blockSignals(True)
            self.x_cm_spinbox.setValue(x_cm)
            self.y_cm_spinbox.setValue(y_cm)
            self.x_cm_spinbox.blockSignals(False)
            self.y_cm_spinbox.blockSignals(False)
            self._set_combo_to_custom()
        self._sync_diagram_from_relative()
        self.changed.emit()

    def _set_combo_to_custom(self) -> None:
        self.position_combo.blockSignals(True)
        self.position_combo.setCurrentText(CUSTOM_POSITION)
        self.position_combo.blockSignals(False)

    def _update_position_labels(self) -> None:
        self.x_value_label.setText(str(self.x_slider.value()))
        self.y_value_label.setText(str(self.y_slider.value()))

    def _sync_diagram(self) -> None:
        if self.relative_position_radio.isChecked():
            self._sync_diagram_from_relative()
        else:
            self._sync_diagram_from_absolute()

    def _sync_diagram_from_relative(self) -> None:
        self.position_diagram.set_origin(self._position_origin)
        self.position_diagram.set_position(
            float(self.x_slider.value()), float(self.y_slider.value())
        )

    def _sync_diagram_from_absolute(self) -> None:
        x_pct, y_pct = self._cm_to_percent(
            self.x_cm_spinbox.value(), self.y_cm_spinbox.value()
        )
        self.position_diagram.set_origin(self._position_origin)
        self.position_diagram.set_position(x_pct, y_pct)

    # -------------------------------------------------------------------- state

    def set_position_origin(self, origin: str) -> None:
        self._position_origin = origin
        self.position_diagram.set_origin(origin)

    def apply_position_preset(self, name: str) -> None:
        """Apply a named preset without emitting a change for each widget."""
        if name not in POSITION_PRESETS:
            return
        x_pct, y_pct, _, origin = POSITION_PRESETS[name]
        self._sync_from_preset = True
        self.set_position_origin(origin)
        self.position_combo.blockSignals(True)
        self.position_combo.setCurrentText(name)
        self.position_combo.blockSignals(False)
        self.x_slider.setValue(int(round(x_pct)))
        self.y_slider.setValue(int(round(y_pct)))
        x_cm, y_cm = self._percent_to_cm(x_pct, y_pct)
        self.x_cm_spinbox.setValue(x_cm)
        self.y_cm_spinbox.setValue(y_cm)
        self._sync_from_preset = False
        self._update_position_labels()
        self._sync_diagram()

    def apply_absolute_y_cm(self, y_cm: float) -> None:
        """
        Switch to absolute mode and place Y at ``y_cm`` from the page bottom.

        Used by the detected-footer hint, which is always measured upward from
        the bottom edge, so a top origin is flipped to the matching bottom one.
        """
        self.absolute_position_radio.setChecked(True)
        self._on_position_mode_changed()

        origin = self._position_origin
        if origin in (ORIGIN_TOP_LEFT, ORIGIN_TOP_RIGHT):
            origin = (
                ORIGIN_BOTTOM_RIGHT
                if origin == ORIGIN_TOP_RIGHT
                else ORIGIN_BOTTOM_LEFT
            )
            self.set_position_origin(origin)

        _, y_pct = self._cm_to_percent(0.0, y_cm)
        self._sync_from_preset = True
        self.y_cm_spinbox.setValue(round(y_cm, 2))
        self.y_slider.setValue(int(round(max(0.0, min(100.0, y_pct)))))
        self._set_combo_to_custom()
        self._sync_from_preset = False

        self._update_position_labels()
        self._sync_diagram_from_absolute()
        self.changed.emit()

    def values(self) -> PositionValues:
        """Current position selection, with the text anchor resolved."""
        position_name = self.position_combo.currentText()
        relative = self.relative_position_radio.isChecked()
        x_pct = float(self.x_slider.value())
        y_pct = float(self.y_slider.value())
        x_cm = self.x_cm_spinbox.value()
        y_cm = self.y_cm_spinbox.value()

        if relative:
            if position_name == CUSTOM_POSITION:
                anchor = self.infer_text_anchor(x_pct)
            else:
                anchor = preset_anchor(position_name)
        else:
            x_pct_for_anchor, _ = self._cm_to_percent(x_cm, y_cm)
            anchor = self.infer_text_anchor(x_pct_for_anchor)

        return PositionValues(
            position_mode=POSITION_RELATIVE if relative else POSITION_ABSOLUTE,
            position_name=position_name,
            position_origin=self._position_origin,
            x_percent=x_pct,
            y_percent=y_pct,
            x_cm=x_cm,
            y_cm=y_cm,
            text_anchor=anchor,
        )

    def set_values(self, values: PositionValues) -> None:
        """Restore a saved selection (session load, or carried between rebuilds)."""
        self._sync_from_preset = True

        if values.position_mode == POSITION_ABSOLUTE:
            self.absolute_position_radio.setChecked(True)
        else:
            self.relative_position_radio.setChecked(True)

        self.position_combo.blockSignals(True)
        if self.position_combo.findText(values.position_name) >= 0:
            self.position_combo.setCurrentText(values.position_name)
        self.position_combo.blockSignals(False)

        self.set_position_origin(values.position_origin)
        self.x_slider.setValue(int(round(values.x_percent)))
        self.y_slider.setValue(int(round(values.y_percent)))
        self.x_cm_spinbox.setValue(values.x_cm)
        self.y_cm_spinbox.setValue(values.y_cm)

        self._sync_from_preset = False
        self._on_position_mode_changed()
        self._update_position_labels()

    def to_dict(self) -> dict:
        """Serialise for session storage."""
        values = self.values()
        return {
            "position_mode": values.position_mode,
            "position_name": values.position_name,
            "position_origin": values.position_origin,
            "x_percent": values.x_percent,
            "y_percent": values.y_percent,
            "x_cm": values.x_cm,
            "y_cm": values.y_cm,
        }

    def load_dict(self, data: dict) -> None:
        """Restore from a session payload, ignoring malformed entries."""
        if not isinstance(data, dict):
            return
        position_name = str(data.get("position_name", DEFAULT_POSITION))
        mode = str(data.get("position_mode", POSITION_RELATIVE))

        # Named presets track the current corner-relative defaults; only Custom
        # (or an unknown name) keeps the exact saved offsets.
        if position_name in POSITION_PRESETS:
            self.apply_position_preset(position_name)
            if mode == POSITION_ABSOLUTE:
                self.absolute_position_radio.setChecked(True)
            else:
                self.relative_position_radio.setChecked(True)
            self._on_position_mode_changed()
            return

        default_x_cm, default_y_cm = self._percent_to_cm(50.0, 5.0)
        self.set_values(
            PositionValues(
                position_mode=mode,
                position_name=position_name,
                position_origin=str(data.get("position_origin", DEFAULT_ORIGIN)),
                x_percent=float(data.get("x_percent", 50.0)),
                y_percent=float(data.get("y_percent", 5.0)),
                x_cm=float(data.get("x_cm", default_x_cm)),
                y_cm=float(data.get("y_cm", default_y_cm)),
                text_anchor="center",
            )
        )
