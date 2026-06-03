"""
Main window module for the Page Numbering Tool application.
Contains the PyQt6 UI components and main application logic.
"""

from typing import List, Optional, Dict, Tuple, Any
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QFileDialog, QLabel, QSpinBox, QMessageBox, QGroupBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QRadioButton,
    QButtonGroup, QCheckBox, QLineEdit, QComboBox, QSlider,
    QDoubleSpinBox, QGridLayout, QFrame, QSizePolicy, QScrollArea,
)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QColor, QFont, QDesktopServices
from pathlib import Path
from datetime import datetime
import json
import sys

from file_handler import FileHandler
from pdf_processor import PDFProcessor
from page_number_config import (
    PageNumberSettings,
    load_font_names,
    minimum_digits_for_page_count,
    POSITION_PRESETS,
    CUSTOM_POSITION,
    DEFAULT_POSITION,
    DEFAULT_FONT,
    DEFAULT_FONT_SIZE,
    DEFAULT_SEPARATOR,
    POSITION_RELATIVE,
    POSITION_ABSOLUTE,
    preset_anchor,
)

from position_diagram import PositionDiagramWidget
from ui_helpers import (
    add_form_row,
    configure_compact_grid,
    prepare_line_edit,
    prepare_spinbox,
    prepare_double_spinbox,
    prepare_combo_box,
    FIELD_MIN_HEIGHT,
)
from session_manager import (
    session_directory,
    build_session_filename,
    save_session,
    load_session,
    windows_username,
)

A4_WIDTH_CM = 21.0
A4_HEIGHT_CM = 29.7


# Pantone 382C - Lime Green (RGB: 206, 220, 0)
LIME_GREEN = QColor(206, 220, 0)


class MainWindow(QMainWindow):
    """
    Main application window for the Page Numbering Tool.
    
    Attributes:
        file_handler: FileHandler instance for file operations
        pdf_processor: PDFProcessor instance for PDF operations
        file_table: QTableWidget for displaying files and assembly options
        file_paths: List of file paths
        main_file_path: Path to the document used as the main body
        insert_after_page: Map of file_path to main-document page after which to insert
    """
    
    def __init__(self, initial_session_path: Optional[Path] = None) -> None:
        """
        Initialize the main window and set up UI components.
        """
        super().__init__()
        self.file_handler: FileHandler = FileHandler()
        self.pdf_processor: PDFProcessor = PDFProcessor()
        self.file_paths: List[str] = []
        self.main_file_path: Optional[str] = None
        self.insert_after_page: Dict[str, int] = {}
        self.buffer_pages: Dict[str, int] = {}
        self.page_counts: Dict[str, int] = {}
        self._main_button_group: Optional[QButtonGroup] = None
        self._position_sync_from_preset = False
        self._current_session_path: Optional[Path] = initial_session_path
        
        self.setWindowTitle("Page Numbering Tool")
        self.setGeometry(100, 100, 900, 820)
        
        self._setup_ui()
        self._apply_lime_green_styling()
        if initial_session_path is not None:
            self._load_session_file(initial_session_path)
    
    def _setup_ui(self) -> None:
        """
        Set up the user interface components.
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        content = QWidget()
        main_layout = QVBoxLayout(content)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        file_section = self._create_file_section()
        main_layout.addWidget(file_section)

        numbering_section = self._create_numbering_section()
        main_layout.addWidget(numbering_section)

        table_section = self._create_table_section()
        main_layout.addWidget(table_section)

        button_layout = self._create_button_section()
        main_layout.addLayout(button_layout)

        self.status_label = QLabel("Ready")
        main_layout.addWidget(self.status_label)

        scroll.setWidget(content)
        self.setCentralWidget(scroll)
    
    def _create_file_section(self) -> QGroupBox:
        """
        Create the file selection section.
        
        Returns:
            QGroupBox containing file selection controls
        """
        group = QGroupBox("File Selection")
        layout = QHBoxLayout()
        
        add_files_btn = QPushButton("Add Files (PDF/Word)")
        add_files_btn.clicked.connect(self._add_files)
        layout.addWidget(add_files_btn)
        
        layout.addStretch()
        
        group.setLayout(layout)
        return group
    
    def _create_numbering_section(self) -> QGroupBox:
        """Create page numbering options and live preview (compact fields)."""
        group = QGroupBox("Page Numbering Options")
        outer = QVBoxLayout()
        outer.setSpacing(6)

        grid = QGridLayout()
        configure_compact_grid(grid)
        row = 0

        self.page_seite_checkbox = QCheckBox('Add "Page" / "Seite" label')
        self.page_seite_checkbox.stateChanged.connect(self._on_numbering_option_changed)
        self.page_seite_checkbox.stateChanged.connect(self._on_page_seite_checkbox_changed)
        grid.addWidget(self.page_seite_checkbox, row, 0, 1, 3)
        row += 1

        label_row = QHBoxLayout()
        label_row.setSpacing(4)
        self.page_radio = QRadioButton("Page")
        self.seite_radio = QRadioButton("Seite")
        self.custom_label_radio = QRadioButton("Custom")
        self.page_radio.setChecked(True)
        self._label_button_group = QButtonGroup(self)
        self._label_button_group.addButton(self.page_radio)
        self._label_button_group.addButton(self.seite_radio)
        self._label_button_group.addButton(self.custom_label_radio)
        self.page_radio.toggled.connect(self._on_label_type_changed)
        self.seite_radio.toggled.connect(self._on_label_type_changed)
        self.custom_label_radio.toggled.connect(self._on_label_type_changed)
        label_row.addWidget(self.page_radio)
        label_row.addWidget(self.seite_radio)
        label_row.addWidget(self.custom_label_radio)
        self.custom_label_edit = prepare_line_edit(QLineEdit(), 120)
        self.custom_label_edit.setPlaceholderText("Custom")
        self.custom_label_edit.setEnabled(False)
        self.custom_label_edit.textChanged.connect(self._on_numbering_option_changed)
        label_row.addWidget(self.custom_label_edit)
        label_row.addStretch()
        label_widget = QWidget()
        label_widget.setLayout(label_row)
        self.page_radio.setEnabled(False)
        self.seite_radio.setEnabled(False)
        self.custom_label_radio.setEnabled(False)
        add_form_row(grid, row, "Label:", label_widget)
        row += 1

        prefix_digits_row = QHBoxLayout()
        prefix_digits_row.setSpacing(12)

        prefix_digits_row.addWidget(QLabel("Chapter prefix:"))
        self.chapter_prefix_edit = prepare_line_edit(QLineEdit(), 50)
        self.chapter_prefix_edit.setPlaceholderText("5, 9.6, 10A…")
        self.chapter_prefix_edit.textChanged.connect(self._on_numbering_option_changed)
        prefix_digits_row.addWidget(self.chapter_prefix_edit, stretch=1)

        prefix_digits_row.addWidget(QLabel("Number of digits:"))
        self.digits_spinbox = prepare_spinbox(QSpinBox(), 56)
        self.digits_spinbox.setMinimum(1)
        self.digits_spinbox.setMaximum(12)
        self.digits_spinbox.setValue(1)
        self.digits_spinbox.setToolTip(
            "Minimum increases with document size (2+ for 10+ pages, 3+ for 100+, etc.)"
        )
        self.digits_spinbox.valueChanged.connect(self._on_numbering_option_changed)
        prefix_digits_row.addWidget(self.digits_spinbox)

        prefix_digits_row.addWidget(QLabel("Separator:"))
        self.separator_edit = prepare_line_edit(QLineEdit(), 52)
        self.separator_edit.setText(DEFAULT_SEPARATOR)
        self.separator_edit.setMaxLength(8)
        self.separator_edit.textChanged.connect(self._on_numbering_option_changed)
        prefix_digits_row.addWidget(self.separator_edit)

        prefix_digits_row.addWidget(QLabel("Suffix:"))
        self.suffix_edit = prepare_line_edit(QLineEdit(), 80)
        self.suffix_edit.setPlaceholderText("e.g. -")
        self.suffix_edit.textChanged.connect(self._on_numbering_option_changed)
        prefix_digits_row.addWidget(self.suffix_edit)

        prefix_digits_widget = QWidget()
        prefix_digits_widget.setLayout(prefix_digits_row)
        grid.addWidget(prefix_digits_widget, row, 0, 1, 3)
        row += 1

        self.white_background_checkbox = QCheckBox("Add white background box?")
        self.white_background_checkbox.stateChanged.connect(self._on_numbering_option_changed)
        grid.addWidget(self.white_background_checkbox, row, 0, 1, 3)
        row += 1

        font_row = QHBoxLayout()
        font_row.setSpacing(12)
        font_row.addWidget(QLabel("Font:"))
        self.font_combo = prepare_combo_box(QComboBox(), 150)
        for font_name in load_font_names():
            self.font_combo.addItem(font_name)
        idx = self.font_combo.findText(DEFAULT_FONT)
        if idx >= 0:
            self.font_combo.setCurrentIndex(idx)
        self.font_combo.currentTextChanged.connect(self._on_numbering_option_changed)
        font_row.addWidget(self.font_combo, stretch=1)

        font_row.addWidget(QLabel("Size:"))
        self.font_size_spinbox = prepare_double_spinbox(QDoubleSpinBox(), 64)
        self.font_size_spinbox.setDecimals(1)
        self.font_size_spinbox.setMinimum(0.1)
        self.font_size_spinbox.setMaximum(72.0)
        self.font_size_spinbox.setSingleStep(0.5)
        self.font_size_spinbox.setValue(DEFAULT_FONT_SIZE)
        self.font_size_spinbox.valueChanged.connect(self._on_numbering_option_changed)
        font_row.addWidget(self.font_size_spinbox)

        font_row.addWidget(QLabel("RGB:"))
        self.color_r_spin = prepare_spinbox(QSpinBox(), 52)
        self.color_g_spin = prepare_spinbox(QSpinBox(), 52)
        self.color_b_spin = prepare_spinbox(QSpinBox(), 52)
        for spin in (self.color_r_spin, self.color_g_spin, self.color_b_spin):
            spin.setRange(0, 255)
            spin.valueChanged.connect(self._on_numbering_option_changed)
        font_row.addWidget(self.color_r_spin)
        font_row.addWidget(self.color_g_spin)
        font_row.addWidget(self.color_b_spin)
        self.color_swatch = QLabel("   ")
        self.color_swatch.setFixedSize(32, FIELD_MIN_HEIGHT - 4)
        self._update_color_swatch()
        font_row.addWidget(self.color_swatch)
        pick_color_btn = QPushButton("Pick…")
        pick_color_btn.setMinimumHeight(FIELD_MIN_HEIGHT)
        pick_color_btn.setToolTip("Pick colour")
        pick_color_btn.clicked.connect(self._pick_font_color)
        font_row.addWidget(pick_color_btn)
        font_row.addStretch()
        font_widget = QWidget()
        font_widget.setLayout(font_row)
        grid.addWidget(font_widget, row, 0, 1, 3)
        row += 1

        self.relative_position_radio = QRadioButton("Relative (%)")
        self.absolute_position_radio = QRadioButton("Absolute (cm)")
        self.relative_position_radio.setChecked(True)
        self._position_mode_group = QButtonGroup(self)
        self._position_mode_group.addButton(self.relative_position_radio)
        self._position_mode_group.addButton(self.absolute_position_radio)
        self.relative_position_radio.toggled.connect(self._on_position_mode_changed)

        location_block = QHBoxLayout()
        location_block.setSpacing(12)
        location_block.addWidget(self.relative_position_radio)
        location_block.addWidget(self.absolute_position_radio)

        loc_controls = QVBoxLayout()
        loc_controls.setSpacing(6)
        self.position_combo = prepare_combo_box(QComboBox(), 160)
        preset_names = list(POSITION_PRESETS.keys()) + [CUSTOM_POSITION]
        self.position_combo.addItems(preset_names)
        self.position_combo.setCurrentText(DEFAULT_POSITION)
        self.position_combo.currentTextChanged.connect(self._on_position_preset_changed)
        loc_controls.addWidget(self.position_combo)

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
        self.x_cm_spinbox.setValue(A4_WIDTH_CM * 0.5)
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
        self.y_cm_spinbox.setValue(A4_HEIGHT_CM * 0.05)
        self.y_cm_spinbox.valueChanged.connect(self._on_absolute_position_changed)
        abs_y_row.addWidget(self.y_cm_spinbox)
        abs_col.addLayout(abs_y_row)

        self._relative_position_widget = QWidget()
        self._relative_position_widget.setLayout(slider_col)
        self._absolute_position_widget = QWidget()
        self._absolute_position_widget.setLayout(abs_col)
        loc_controls.addWidget(self._relative_position_widget)
        loc_controls.addWidget(self._absolute_position_widget)
        self._absolute_position_widget.hide()
        location_block.addLayout(loc_controls, stretch=1)

        self.position_diagram = PositionDiagramWidget()
        location_block.addWidget(self.position_diagram)

        location_widget = QWidget()
        location_widget.setLayout(location_block)
        add_form_row(grid, row, "Position:", location_widget)
        row += 1

        outer.addLayout(grid)

        preview_frame = QFrame()
        preview_frame.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Sunken)
        preview_layout = QHBoxLayout()
        preview_layout.setContentsMargins(6, 4, 6, 4)
        preview_lbl = QLabel("Preview:")
        preview_lbl.setFixedWidth(130)
        preview_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        preview_layout.addWidget(preview_lbl)
        self.preview_label = QLabel("001")
        self.preview_label.setFont(QFont(DEFAULT_FONT, int(DEFAULT_FONT_SIZE)))
        self.preview_label.setContentsMargins(8, 4, 8, 4)
        preview_layout.addWidget(self.preview_label)
        preview_layout.addStretch()
        preview_frame.setLayout(preview_layout)
        outer.addWidget(preview_frame)

        group.setLayout(outer)
        self._apply_position_preset(DEFAULT_POSITION)
        self._update_numbering_preview()
        return group

    def _on_page_seite_checkbox_changed(self) -> None:
        enabled = self.page_seite_checkbox.isChecked()
        self.page_radio.setEnabled(enabled)
        self.seite_radio.setEnabled(enabled)
        self.custom_label_radio.setEnabled(enabled)
        self._on_label_type_changed()
        self._on_numbering_option_changed()

    def _on_label_type_changed(self) -> None:
        custom_on = (
            self.page_seite_checkbox.isChecked() and self.custom_label_radio.isChecked()
        )
        self.custom_label_edit.setEnabled(custom_on)
        self._on_numbering_option_changed()

    def _on_position_mode_changed(self) -> None:
        relative = self.relative_position_radio.isChecked()
        self._relative_position_widget.setVisible(relative)
        self._absolute_position_widget.setVisible(not relative)
        self.position_combo.setEnabled(relative)
        self.x_slider.setEnabled(relative)
        self.y_slider.setEnabled(relative)
        if relative:
            self._sync_diagram_from_relative()
        else:
            self._sync_diagram_from_absolute()
        self._on_numbering_option_changed()

    def _on_absolute_position_changed(self) -> None:
        if not self._position_sync_from_preset:
            x_cm = self.x_cm_spinbox.value()
            y_cm = self.y_cm_spinbox.value()
            x_pct, y_pct = self._cm_to_percent(x_cm, y_cm)
            self.x_slider.blockSignals(True)
            self.y_slider.blockSignals(True)
            self.x_slider.setValue(int(round(x_pct)))
            self.y_slider.setValue(int(round(y_pct)))
            self.x_slider.blockSignals(False)
            self.y_slider.blockSignals(False)
            self._update_position_labels()
            self.position_combo.blockSignals(True)
            self.position_combo.setCurrentText(CUSTOM_POSITION)
            self.position_combo.blockSignals(False)
        self._sync_diagram_from_absolute()
        self._on_numbering_option_changed()

    def _sync_diagram_from_relative(self) -> None:
        self.position_diagram.set_position(
            float(self.x_slider.value()), float(self.y_slider.value())
        )

    def _sync_diagram_from_absolute(self) -> None:
        x_pct = (self.x_cm_spinbox.value() / A4_WIDTH_CM) * 100.0
        y_pct = (self.y_cm_spinbox.value() / A4_HEIGHT_CM) * 100.0
        self.position_diagram.set_position(x_pct, y_pct)

    def _percent_to_cm(self, x_pct: float, y_pct: float) -> Tuple[float, float]:
        return (x_pct / 100.0 * A4_WIDTH_CM, y_pct / 100.0 * A4_HEIGHT_CM)

    def _cm_to_percent(self, x_cm: float, y_cm: float) -> Tuple[float, float]:
        return (x_cm / A4_WIDTH_CM * 100.0, y_cm / A4_HEIGHT_CM * 100.0)

    def _update_color_swatch(self) -> None:
        r, g, b = self.color_r_spin.value(), self.color_g_spin.value(), self.color_b_spin.value()
        self.color_swatch.setStyleSheet(
            f"background-color: rgb({r}, {g}, {b}); border: 1px solid #888;"
        )

    def _pick_font_color(self) -> None:
        from PyQt6.QtWidgets import QColorDialog

        initial = QColor(
            self.color_r_spin.value(),
            self.color_g_spin.value(),
            self.color_b_spin.value(),
        )
        color = QColorDialog.getColor(initial, self, "Select font colour")
        if color.isValid():
            self.color_r_spin.setValue(color.red())
            self.color_g_spin.setValue(color.green())
            self.color_b_spin.setValue(color.blue())
            self._update_color_swatch()
            self._on_numbering_option_changed()

    def _on_position_preset_changed(self, name: str) -> None:
        if name != CUSTOM_POSITION and name in POSITION_PRESETS:
            self._position_sync_from_preset = True
            x_pct, y_pct, _ = POSITION_PRESETS[name]
            self.x_slider.setValue(int(round(x_pct)))
            self.y_slider.setValue(int(round(y_pct)))
            x_cm, y_cm = self._percent_to_cm(x_pct, y_pct)
            self.x_cm_spinbox.setValue(x_cm)
            self.y_cm_spinbox.setValue(y_cm)
            self._position_sync_from_preset = False
            self._update_position_labels()
            if self.relative_position_radio.isChecked():
                self._sync_diagram_from_relative()
            else:
                self._sync_diagram_from_absolute()
        self._on_numbering_option_changed()

    def _on_position_slider_changed(self) -> None:
        self._update_position_labels()
        x = float(self.x_slider.value())
        y = float(self.y_slider.value())
        if not self._position_sync_from_preset:
            x_cm, y_cm = self._percent_to_cm(x, y)
            self.x_cm_spinbox.blockSignals(True)
            self.y_cm_spinbox.blockSignals(True)
            self.x_cm_spinbox.setValue(x_cm)
            self.y_cm_spinbox.setValue(y_cm)
            self.x_cm_spinbox.blockSignals(False)
            self.y_cm_spinbox.blockSignals(False)
            self.position_combo.blockSignals(True)
            self.position_combo.setCurrentText(CUSTOM_POSITION)
            self.position_combo.blockSignals(False)
        self._sync_diagram_from_relative()
        self._on_numbering_option_changed()

    def _update_position_labels(self) -> None:
        self.x_value_label.setText(str(self.x_slider.value()))
        self.y_value_label.setText(str(self.y_slider.value()))

    def _apply_position_preset(self, name: str) -> None:
        if name in POSITION_PRESETS:
            x_pct, y_pct, _ = POSITION_PRESETS[name]
            self.x_slider.setValue(int(round(x_pct)))
            self.y_slider.setValue(int(round(y_pct)))
            x_cm, y_cm = self._percent_to_cm(x_pct, y_pct)
            self.x_cm_spinbox.setValue(x_cm)
            self.y_cm_spinbox.setValue(y_cm)
            self._sync_diagram_from_relative()
            self._update_position_labels()

    def _get_total_page_count(self) -> int:
        if not self.main_file_path:
            return 0
        main_pages = self.page_counts.get(self.main_file_path, 0)
        total_pages = main_pages
        for file_path in self.file_paths:
            if file_path == self.main_file_path:
                continue
            total_pages += self.page_counts.get(file_path, 0)
            total_pages += self.buffer_pages.get(file_path, 0)
        return total_pages

    def _update_digits_minimum(self) -> None:
        total = self._get_total_page_count()
        min_digits = minimum_digits_for_page_count(total)
        self.digits_spinbox.setMinimum(min_digits)
        if self.digits_spinbox.value() < min_digits:
            self.digits_spinbox.setValue(min_digits)

    def _on_numbering_option_changed(self, *_args) -> None:
        self._update_numbering_preview()

    def _update_numbering_preview(self) -> None:
        settings = self._get_page_number_settings()
        preview_text = settings.format_number(1)
        self.preview_label.setText(preview_text)
        preview_font = QFont(self.font_combo.currentText(), int(self.font_size_spinbox.value()))
        self.preview_label.setFont(preview_font)
        r, g, b = settings.font_color_rgb
        bg_style = (
            "background-color: white; padding: 4px 8px;"
            if settings.use_white_background
            else "background-color: transparent; padding: 4px 8px;"
        )
        self.preview_label.setStyleSheet(f"color: rgb({r}, {g}, {b}); {bg_style}")
        self._update_color_swatch()

    def _infer_text_anchor(self, x_percent: float) -> str:
        if x_percent <= 20.0:
            return "left"
        if x_percent >= 80.0:
            return "right"
        return "center"

    def _resolve_label_text(self) -> str:
        if self.seite_radio.isChecked():
            return "Seite"
        if self.custom_label_radio.isChecked():
            return self.custom_label_edit.text().strip() or "Custom"
        return "Page"

    def _get_page_number_settings(self) -> PageNumberSettings:
        position_name = self.position_combo.currentText()
        relative = self.relative_position_radio.isChecked()
        x_pct = float(self.x_slider.value())
        y_pct = float(self.y_slider.value())
        x_cm = self.x_cm_spinbox.value()
        y_cm = self.y_cm_spinbox.value()

        if relative:
            if position_name == CUSTOM_POSITION:
                anchor = self._infer_text_anchor(x_pct)
            else:
                anchor = preset_anchor(position_name)
        else:
            x_pct_for_anchor, _ = self._cm_to_percent(x_cm, y_cm)
            anchor = self._infer_text_anchor(x_pct_for_anchor)

        sep = self.separator_edit.text()
        if not sep:
            sep = DEFAULT_SEPARATOR

        return PageNumberSettings(
            use_label=self.page_seite_checkbox.isChecked(),
            label_text=self._resolve_label_text(),
            chapter_prefix=self.chapter_prefix_edit.text(),
            num_digits=self.digits_spinbox.value(),
            separator=sep,
            position_name=position_name,
            position_mode=POSITION_RELATIVE if relative else POSITION_ABSOLUTE,
            x_percent=x_pct,
            y_percent=y_pct,
            x_cm=x_cm,
            y_cm=y_cm,
            text_anchor=anchor,
            font_name=self.font_combo.currentText(),
            font_size=self.font_size_spinbox.value(),
            font_color_rgb=(
                self.color_r_spin.value(),
                self.color_g_spin.value(),
                self.color_b_spin.value(),
            ),
            suffix=self.suffix_edit.text(),
            use_white_background=self.white_background_checkbox.isChecked(),
        )

    def _create_table_section(self) -> QGroupBox:
        """
        Create the file table section with ordering and buffer pages.
        
        Returns:
            QGroupBox containing the file table
        """
        group = QGroupBox("Document Assembly")
        layout = QVBoxLayout()
        
        info_label = QLabel(
            "Select one file as the main document. For each other file, choose the main-document "
            "page after which it should be inserted (0 = beginning). Buffer pages add blank pages "
            "after each inserted file."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        table_toolbar = QHBoxLayout()
        remove_file_btn = QPushButton("Remove Selected")
        remove_file_btn.clicked.connect(self._remove_selected_files)
        table_toolbar.addWidget(remove_file_btn)
        table_toolbar.addStretch()
        layout.addLayout(table_toolbar)
        
        self.file_table = QTableWidget()
        self.file_table.setColumnCount(5)
        self.file_table.setHorizontalHeaderLabels(
            ["Main", "File Name", "Pages", "Insert After Page", "Buffer Pages"]
        )
        
        header = self.file_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        
        self.file_table.setColumnWidth(0, 60)
        self.file_table.setColumnWidth(2, 80)
        self.file_table.setColumnWidth(3, 150)
        self.file_table.setColumnWidth(4, 150)
        
        self.file_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.file_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.file_table.setMinimumHeight(300)
        
        layout.addWidget(self.file_table)
        
        # Total pages label
        self.total_pages_label = QLabel("Total Pages: 0")
        self.total_pages_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        layout.addWidget(self.total_pages_label)

        session_row = QHBoxLayout()
        save_session_btn = QPushButton("Save Session")
        save_session_btn.clicked.connect(self._save_session)
        session_row.addWidget(save_session_btn)
        session_row.addStretch()
        layout.addLayout(session_row)
        
        group.setLayout(layout)
        return group
    
    def _create_button_section(self) -> QHBoxLayout:
        """
        Create the action buttons section.
        
        Returns:
            QHBoxLayout containing action buttons
        """
        layout = QHBoxLayout()
        
        self.process_btn = QPushButton("Add Page Numbers & Combine")
        self.process_btn.clicked.connect(self._process_files)
        self.process_btn.setEnabled(False)
        self.process_btn.setMinimumHeight(50)
        self.process_btn.setMinimumWidth(250)
        layout.addWidget(self.process_btn)
        
        layout.addStretch()
        
        return layout
    
    def _apply_lime_green_styling(self) -> None:
        """
        Apply lime green (Pantone 382C) styling to key elements.
        """
        lime_green_hex = LIME_GREEN.name()
        self.setStyleSheet(f"""
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
                min-height: {FIELD_MIN_HEIGHT}px;
                padding: 4px 8px;
            }}
            QSpinBox::up-button, QSpinBox::down-button,
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
                width: 18px;
            }}
        """)
        self.process_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {lime_green_hex};
                color: #000000;
                font-weight: bold;
                font-size: 14px;
                border: 2px solid {lime_green_hex};
                border-radius: 5px;
                padding: 10px;
            }}
            QPushButton:hover {{
                background-color: #B8C800;
                border-color: #B8C800;
            }}
            QPushButton:disabled {{
                background-color: #E0E0E0;
                color: #808080;
                border-color: #E0E0E0;
            }}
        """)
    
    def _add_files(self) -> None:
        """
        Open file dialog to add files to the table.
        """
        file_dialog = QFileDialog()
        file_dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
        file_dialog.setNameFilter("Documents (*.pdf *.docx *.doc)")
        
        if file_dialog.exec():
            selected_files = file_dialog.selectedFiles()
            valid_files, invalid_files = self.file_handler.validate_files(selected_files)
            
            for file_path in valid_files:
                # Check if file is already in the table
                if file_path not in self.file_paths:
                    self._add_file_to_table(file_path)
            
            if invalid_files:
                QMessageBox.warning(
                    self,
                    "Invalid Files",
                    f"The following files were not added:\n" + "\n".join(invalid_files)
                )
            
            self._update_process_button_state()
            self._update_status(f"Added {len(valid_files)} file(s)")
    
    def _add_file_to_table(self, file_path: str) -> None:
        """
        Add a file to the list and refresh the table.
        
        Args:
            file_path: Path to the file
        """
        if file_path not in self.file_paths:
            self.file_paths.append(file_path)
            self.buffer_pages[file_path] = 0
            self.insert_after_page[file_path] = 0
            page_count = self.pdf_processor.get_page_count(file_path)
            self.page_counts[file_path] = page_count
            if self.main_file_path is None:
                self.main_file_path = file_path
            self._refresh_table()
            self._update_total_pages()
    
    def _refresh_table(self) -> None:
        """
        Rebuild the entire table from the file_paths list.
        """
        self.file_table.setRowCount(0)
        
        if self._main_button_group is not None:
            self._main_button_group.deleteLater()
        self._main_button_group = QButtonGroup(self)
        self._main_button_group.setExclusive(True)
        
        main_page_count = 0
        if self.main_file_path:
            main_page_count = self.page_counts.get(self.main_file_path, 0)
        
        for file_path in self.file_paths:
            row = self.file_table.rowCount()
            self.file_table.insertRow(row)
            is_main = file_path == self.main_file_path
            
            main_radio = QRadioButton()
            main_radio.blockSignals(True)
            main_radio.setChecked(is_main)
            main_radio.blockSignals(False)
            main_radio.toggled.connect(
                lambda checked, fp=file_path: self._on_main_radio_toggled(fp, checked)
            )
            self._main_button_group.addButton(main_radio)
            
            main_widget = QWidget()
            main_layout = QHBoxLayout()
            main_layout.setContentsMargins(12, 2, 5, 2)
            main_layout.addWidget(main_radio)
            main_widget.setLayout(main_layout)
            self.file_table.setCellWidget(row, 0, main_widget)
            
            file_name = self.file_handler.get_file_name(file_path)
            name_item = QTableWidgetItem(file_name)
            name_item.setData(Qt.ItemDataRole.UserRole, file_path)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.file_table.setItem(row, 1, name_item)
            
            if file_path not in self.page_counts:
                page_count = self.pdf_processor.get_page_count(file_path, convert_word=True)
                self.page_counts[file_path] = page_count
            else:
                page_count = self.page_counts[file_path]
            
            pages_item = QTableWidgetItem(str(page_count))
            pages_item.setFlags(Qt.ItemFlag.NoItemFlags)
            pages_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.file_table.setItem(row, 2, pages_item)
            
            insert_spinbox = QSpinBox()
            insert_spinbox.setMinimum(0)
            insert_spinbox.setMaximum(max(main_page_count, 0))
            insert_spinbox.setValue(self.insert_after_page.get(file_path, 0))
            insert_spinbox.setToolTip(
                "Main-document page after which this file is inserted (0 = beginning)"
            )
            insert_spinbox.setEnabled(not is_main)
            insert_spinbox.valueChanged.connect(
                lambda value, fp=file_path: self._update_insert_after_page(fp, value)
            )
            self.file_table.setCellWidget(row, 3, insert_spinbox)
            
            buffer_spinbox = QSpinBox()
            buffer_spinbox.setMinimum(0)
            buffer_spinbox.setMaximum(100)
            buffer_spinbox.setValue(self.buffer_pages.get(file_path, 0))
            buffer_spinbox.setSuffix(" pages")
            buffer_spinbox.setEnabled(not is_main)
            buffer_spinbox.valueChanged.connect(
                lambda value, fp=file_path: self._update_buffer_pages(fp, value)
            )
            self.file_table.setCellWidget(row, 4, buffer_spinbox)
        
        self._update_total_pages()
    
    def _on_main_radio_toggled(self, file_path: str, checked: bool) -> None:
        """
        Handle main-document radio selection.
        
        Args:
            file_path: Path associated with the radio button
            checked: Whether the radio button is selected
        """
        if checked:
            self._set_main_file(file_path)
    
    def _set_main_file(self, file_path: str) -> None:
        """
        Set the main document and refresh dependent controls.
        
        Args:
            file_path: Path to the file that becomes the main document
        """
        if file_path not in self.file_paths or file_path == self.main_file_path:
            return
        
        self.main_file_path = file_path
        self._refresh_table()
    
    def _update_insert_after_page(self, file_path: str, value: int) -> None:
        """
        Update the insert-after-page value for a file.
        
        Args:
            file_path: Path to the file
            value: Main-document page number after which to insert
        """
        self.insert_after_page[file_path] = value
        self._update_total_pages()
    
    def _update_buffer_pages(self, file_path: str, value: int) -> None:
        """
        Update the buffer pages count for a file.
        
        Args:
            file_path: Path to the file
            value: New buffer pages value
        """
        self.buffer_pages[file_path] = value
        self._update_total_pages()
    
    def _remove_selected_files(self) -> None:
        """
        Remove the selected rows from the table.
        """
        selected_rows = sorted(set(item.row() for item in self.file_table.selectedItems()), reverse=True)
        
        if not selected_rows:
            QMessageBox.information(
                self,
                "No Selection",
                "Please select one or more files to remove."
            )
            return
        
        # Remove files from the list in reverse order
        for row in selected_rows:
            if row < len(self.file_paths):
                file_path = self.file_paths[row]
                # Remove from file_paths
                del self.file_paths[row]
                # Remove from buffer_pages
                if file_path in self.buffer_pages:
                    del self.buffer_pages[file_path]
                # Remove from page_counts
                if file_path in self.page_counts:
                    del self.page_counts[file_path]
                if file_path in self.insert_after_page:
                    del self.insert_after_page[file_path]
                if self.main_file_path == file_path:
                    self.main_file_path = self.file_paths[0] if self.file_paths else None
        
        self._refresh_table()
        self._update_total_pages()
        
        self._update_process_button_state()
        self._update_status(f"Removed {len(selected_rows)} file(s)")
    
    
    def _update_process_button_state(self) -> None:
        """
        Update the enabled state of the process button based on file count.
        """
        self.process_btn.setEnabled(len(self.file_paths) > 0)
    
    def _update_status(self, message: str) -> None:
        """
        Update the status label with a message.
        
        Args:
            message: Status message to display
        """
        self.status_label.setText(message)
    
    def _update_total_pages(self) -> None:
        """
        Calculate and update the total page count for the assembled document.
        """
        if not self.main_file_path:
            self.total_pages_label.setText("Total Pages: 0")
            return
        
        main_pages = self.page_counts.get(self.main_file_path, 0)
        total_pages = main_pages
        
        for file_path in self.file_paths:
            if file_path == self.main_file_path:
                continue
            total_pages += self.page_counts.get(file_path, 0)
            total_pages += self.buffer_pages.get(file_path, 0)
        
        self.total_pages_label.setText(f"Total Pages: {total_pages}")
        self._update_digits_minimum()
        self._update_numbering_preview()
    
    def _get_insertions(self) -> List[Tuple[str, int]]:
        """
        Get files to insert into the main document with their page positions.
        
        Returns:
            List of (file_path, after_page) tuples for non-main files
        """
        insertions: List[Tuple[str, int]] = []
        for file_path in self.file_paths:
            if file_path == self.main_file_path:
                continue
            insertions.append((file_path, self.insert_after_page.get(file_path, 0)))
        return insertions
    
    def _process_files(self) -> None:
        """
        Process files: assemble around main document, merge, and add page numbers.
        """
        if len(self.file_paths) == 0:
            QMessageBox.warning(self, "No Files", "Please add files before processing.")
            return
        
        if not self.main_file_path:
            QMessageBox.warning(
                self,
                "No Main Document",
                "Please select a main document before processing."
            )
            return
        
        output_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Combined PDF",
            "",
            "PDF Files (*.pdf)"
        )
        
        if not output_path:
            return
        
        try:
            self._update_status("Processing files...")
            self.process_btn.setEnabled(False)
            
            file_paths = self.file_paths.copy()
            insertions = self._get_insertions()
            from pathlib import Path
            pre_converted_pdfs: Dict[str, str] = {}
            
            self._update_status("Converting Word files to PDF...")
            for file_path in file_paths:
                file_ext = Path(file_path).suffix.lower()
                if file_ext in ['.docx', '.doc']:
                    try:
                        temp_pdf = self.pdf_processor.convert_word_to_pdf(file_path)
                        pre_converted_pdfs[file_path] = temp_pdf
                    except Exception as e:
                        self._update_status(
                            f"Error: Failed to convert {Path(file_path).name}: {str(e)}"
                        )
                        raise
            
            self._update_status("Assembling document and adding page numbers...")
            page_settings = self._get_page_number_settings()
            
            if len(file_paths) == 1:
                self.pdf_processor.process_files(
                    file_paths,
                    [self.buffer_pages.get(file_paths[0], 0)],
                    output_path,
                    start_page_number=1,
                    pre_converted_pdfs=pre_converted_pdfs,
                    page_number_settings=page_settings,
                )
            else:
                self.pdf_processor.process_files_with_main(
                    self.main_file_path,
                    insertions,
                    self.buffer_pages,
                    output_path,
                    start_page_number=1,
                    pre_converted_pdfs=pre_converted_pdfs,
                    page_number_settings=page_settings,
                )
            
            self._update_status(f"Success! Combined PDF saved to: {output_path}")
            self._show_success_dialog(output_path)
            
        except Exception as e:
            self._update_status(f"Error: {str(e)}")
            QMessageBox.critical(
                self,
                "Error",
                f"An error occurred while processing files:\n{str(e)}"
            )
        finally:
            self.process_btn.setEnabled(True)

    def _show_success_dialog(self, output_path: str) -> None:
        """Show success message with option to open the generated PDF."""
        msg = QMessageBox(self)
        msg.setWindowTitle("Success")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText("Files have been combined and page numbers added.")
        msg.setInformativeText(f"Saved to:\n{output_path}")
        open_btn = msg.addButton("Open PDF", QMessageBox.ButtonRole.ActionRole)
        msg.addButton(QMessageBox.StandardButton.Ok)
        msg.exec()
        if msg.clickedButton() == open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(output_path).resolve())))

    def _label_type_key(self) -> str:
        if self.custom_label_radio.isChecked():
            return "custom"
        if self.seite_radio.isChecked():
            return "seite"
        return "page"

    def _export_session_data(self) -> Dict[str, Any]:
        return {
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "username": windows_username(),
            "files": {
                "file_paths": list(self.file_paths),
                "main_file_path": self.main_file_path,
                "insert_after_page": dict(self.insert_after_page),
                "buffer_pages": dict(self.buffer_pages),
            },
            "numbering": {
                "use_label": self.page_seite_checkbox.isChecked(),
                "label_type": self._label_type_key(),
                "custom_label": self.custom_label_edit.text(),
                "chapter_prefix": self.chapter_prefix_edit.text(),
                "num_digits": self.digits_spinbox.value(),
                "separator": self.separator_edit.text(),
                "suffix": self.suffix_edit.text(),
                "use_white_background": self.white_background_checkbox.isChecked(),
                "position_mode": (
                    POSITION_RELATIVE
                    if self.relative_position_radio.isChecked()
                    else POSITION_ABSOLUTE
                ),
                "position_name": self.position_combo.currentText(),
                "x_percent": self.x_slider.value(),
                "y_percent": self.y_slider.value(),
                "x_cm": self.x_cm_spinbox.value(),
                "y_cm": self.y_cm_spinbox.value(),
                "font_name": self.font_combo.currentText(),
                "font_size": self.font_size_spinbox.value(),
                "font_color_rgb": [
                    self.color_r_spin.value(),
                    self.color_g_spin.value(),
                    self.color_b_spin.value(),
                ],
            },
        }

    def _save_session(self) -> None:
        """Save current UI state to a session JSON file."""
        try:
            folder = session_directory()
            if self._current_session_path is not None:
                path = self._current_session_path
            else:
                filename = build_session_filename(self.main_file_path)
                path = folder / filename
            save_session(path, self._export_session_data())
            self._current_session_path = path
            self._update_status(f"Session saved: {path.name}")
            QMessageBox.information(
                self,
                "Session Saved",
                f"Session saved to:\n{path}",
            )
        except OSError as e:
            QMessageBox.critical(self, "Save Failed", f"Could not save session:\n{e}")

    def _load_session_file(self, path: Path) -> None:
        try:
            data = load_session(path)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            QMessageBox.critical(
                self,
                "Load Failed",
                f"Could not load session:\n{path}\n\n{e}",
            )
            return
        self._apply_session_data(data)
        self._current_session_path = path
        self._update_status(f"Loaded session: {path.name}")

    def _apply_session_data(self, data: Dict[str, Any]) -> None:
        files = data.get("files", {})
        numbering = data.get("numbering", {})

        self.file_paths.clear()
        self.insert_after_page.clear()
        self.buffer_pages.clear()
        self.page_counts.clear()
        self.main_file_path = None

        missing: List[str] = []
        saved_paths: List[str] = files.get("file_paths", [])
        for file_path in saved_paths:
            if Path(file_path).is_file():
                self._add_file_to_table(file_path)
            else:
                missing.append(file_path)

        main_path = files.get("main_file_path")
        if main_path and main_path in self.file_paths:
            self.main_file_path = main_path
        elif self.file_paths:
            self.main_file_path = self.file_paths[0]

        for file_path in self.file_paths:
            ins = files.get("insert_after_page", {})
            buf = files.get("buffer_pages", {})
            if file_path in ins:
                self.insert_after_page[file_path] = int(ins[file_path])
            if file_path in buf:
                self.buffer_pages[file_path] = int(buf[file_path])

        self._refresh_table()

        self.page_seite_checkbox.setChecked(bool(numbering.get("use_label", False)))
        label_type = numbering.get("label_type", "page")
        if label_type == "seite":
            self.seite_radio.setChecked(True)
        elif label_type == "custom":
            self.custom_label_radio.setChecked(True)
        else:
            self.page_radio.setChecked(True)
        self.custom_label_edit.setText(str(numbering.get("custom_label", "")))
        self.chapter_prefix_edit.setText(str(numbering.get("chapter_prefix", "")))
        self.digits_spinbox.setValue(int(numbering.get("num_digits", 1)))
        self.separator_edit.setText(str(numbering.get("separator", DEFAULT_SEPARATOR)))
        self.suffix_edit.setText(str(numbering.get("suffix", "")))
        self.white_background_checkbox.setChecked(
            bool(numbering.get("use_white_background", False))
        )

        if numbering.get("position_mode") == POSITION_ABSOLUTE:
            self.absolute_position_radio.setChecked(True)
        else:
            self.relative_position_radio.setChecked(True)

        pos_name = numbering.get("position_name", DEFAULT_POSITION)
        if self.position_combo.findText(pos_name) >= 0:
            self.position_combo.setCurrentText(pos_name)

        self.x_slider.setValue(int(numbering.get("x_percent", 50)))
        self.y_slider.setValue(int(numbering.get("y_percent", 5)))
        self.x_cm_spinbox.setValue(float(numbering.get("x_cm", A4_WIDTH_CM * 0.5)))
        self.y_cm_spinbox.setValue(float(numbering.get("y_cm", A4_HEIGHT_CM * 0.05)))

        font_name = str(numbering.get("font_name", DEFAULT_FONT))
        if self.font_combo.findText(font_name) >= 0:
            self.font_combo.setCurrentText(font_name)
        self.font_size_spinbox.setValue(float(numbering.get("font_size", DEFAULT_FONT_SIZE)))

        rgb = numbering.get("font_color_rgb", [0, 0, 0])
        if isinstance(rgb, (list, tuple)) and len(rgb) >= 3:
            self.color_r_spin.setValue(int(rgb[0]))
            self.color_g_spin.setValue(int(rgb[1]))
            self.color_b_spin.setValue(int(rgb[2]))

        self._on_position_mode_changed()
        self._update_position_labels()
        self._update_digits_minimum()
        self._update_numbering_preview()
        self._update_process_button_state()

        if missing:
            QMessageBox.warning(
                self,
                "Missing Files",
                "The following file paths from the session were not found "
                "and were skipped:\n\n" + "\n".join(missing),
            )

