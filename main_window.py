"""
Main window module for The Reportinator application.
Contains the PyQt6 UI components and main application logic.
"""

from typing import List, Optional, Dict, Tuple, Any, Callable
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QFileDialog, QLabel, QSpinBox, QMessageBox, QGroupBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QRadioButton,
    QButtonGroup, QCheckBox, QLineEdit, QComboBox, QSlider,
    QDoubleSpinBox, QGridLayout, QFrame, QSizePolicy, QScrollArea,
    QProgressDialog, QApplication, QDialog,
)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QColor, QFont, QDesktopServices
from pathlib import Path
from datetime import datetime
import json
import sys

from file_handler import FileHandler
from pdf_processor import PDFProcessor, FooterLineHint
from page_spec import (
    InsertionSegment,
    count_pages_in_spec,
    format_pages_display,
)
from split_dialog import SplitPagesDialog
from page_number_config import (
    PageNumberSettings,
    load_font_names,
    minimum_digits_for_page_count,
    POSITION_PRESETS,
    CUSTOM_POSITION,
    DEFAULT_POSITION,
    DEFAULT_ORIGIN,
    DEFAULT_FONT,
    DEFAULT_FONT_SIZE,
    DEFAULT_SEPARATOR,
    POSITION_RELATIVE,
    POSITION_ABSOLUTE,
    ORIGIN_BOTTOM_RIGHT,
    ORIGIN_TOP_RIGHT,
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
from toc_handler import TocInfo, extract_toc

A4_WIDTH_CM = 21.0
A4_HEIGHT_CM = 29.7


# Pantone 382C - Lime Green (RGB: 206, 220, 0)
LIME_GREEN = QColor(206, 220, 0)


class MainWindow(QMainWindow):
    """
    Main application window for The Reportinator.
    
    Attributes:
        file_handler: FileHandler instance for file operations
        pdf_processor: PDFProcessor instance for PDF operations
        file_table: QTableWidget for displaying files and assembly options
        insertions: Ordered list of insertion segments (file + optional page split)
        main_file_path: Path to the document used as the main body
    """
    
    def __init__(self, initial_session_path: Optional[Path] = None) -> None:
        """
        Initialize the main window and set up UI components.
        """
        super().__init__()
        self.file_handler: FileHandler = FileHandler()
        self.pdf_processor: PDFProcessor = PDFProcessor()
        self.insertions: List[InsertionSegment] = []
        self.main_file_path: Optional[str] = None
        self.page_counts: Dict[str, int] = {}
        self.toc_info: Optional[TocInfo] = None
        self._toc_main_path: Optional[str] = None
        self._insert_controls: Dict[str, Tuple[QComboBox, QSpinBox]] = {}
        self._position_sync_from_preset = False
        self._position_origin: str = DEFAULT_ORIGIN
        self._footer_hint: Optional[FooterLineHint] = None
        self._current_session_path: Optional[Path] = initial_session_path
        
        self.setWindowTitle("The Reportinator")
        self.setGeometry(100, 100, 1100, 820)
        
        self._setup_ui()
        self._apply_lime_green_styling()
        self.position_diagram.set_origin(self._position_origin)
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

        toc_section = self._create_toc_section()
        main_layout.addWidget(toc_section)

        table_section = self._create_table_section()
        main_layout.addWidget(table_section)

        numbering_section = self._create_numbering_section()
        main_layout.addWidget(numbering_section)

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
        group = QGroupBox("Main Document (Word)")
        layout = QVBoxLayout()

        button_row = QHBoxLayout()
        select_main_btn = QPushButton("Select Main Word Document")
        select_main_btn.clicked.connect(self._select_main_document)
        button_row.addWidget(select_main_btn)

        self.add_insertion_files_btn = QPushButton("Add Files to Insert")
        self.add_insertion_files_btn.clicked.connect(self._add_insertion_files)
        self.add_insertion_files_btn.setEnabled(False)
        button_row.addWidget(self.add_insertion_files_btn)
        button_row.addStretch()
        layout.addLayout(button_row)

        self.main_file_label = QLabel(
            "No main document selected. Choose a Word file (.docx / .doc)."
        )
        self.main_file_label.setWordWrap(True)
        layout.addWidget(self.main_file_label)
        
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
        self.footer_hint_label = QLabel(
            "Footer Y: load a main document to estimate the last footer line."
        )
        self.footer_hint_label.setWordWrap(True)
        self.footer_hint_label.setMinimumWidth(130)
        self.footer_hint_label.setMaximumWidth(180)
        self.footer_hint_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self.footer_hint_label.setStyleSheet("color: #555; font-size: 11px;")
        diagram_row = QHBoxLayout()
        diagram_row.setSpacing(8)
        diagram_row.addWidget(self.position_diagram)
        diagram_row.addWidget(self.footer_hint_label, stretch=1)
        location_block.addLayout(diagram_row)

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
        self.position_diagram.set_origin(self._position_origin)
        self.position_diagram.set_position(
            float(self.x_slider.value()), float(self.y_slider.value())
        )

    def _sync_diagram_from_absolute(self) -> None:
        x_pct = (self.x_cm_spinbox.value() / A4_WIDTH_CM) * 100.0
        y_pct = (self.y_cm_spinbox.value() / A4_HEIGHT_CM) * 100.0
        self.position_diagram.set_origin(self._position_origin)
        self.position_diagram.set_position(x_pct, y_pct)

    def _percent_to_cm(self, x_pct: float, y_pct: float) -> Tuple[float, float]:
        return (x_pct / 100.0 * A4_WIDTH_CM, y_pct / 100.0 * A4_HEIGHT_CM)

    def _cm_to_percent(self, x_cm: float, y_cm: float) -> Tuple[float, float]:
        return (x_cm / A4_WIDTH_CM * 100.0, y_cm / A4_HEIGHT_CM * 100.0)

    def _set_position_origin(self, origin: str) -> None:
        self._position_origin = origin
        self.position_diagram.set_origin(origin)

    def _update_footer_hint_label(self) -> None:
        """Refresh the footer Y hint shown beside the position diagram."""
        hint = self._footer_hint
        if hint is None:
            if self.main_file_path:
                self.footer_hint_label.setText(
                    "Footer Y: no footer text found on the middle page."
                )
                self.footer_hint_label.setToolTip(
                    "Checked the bottom band of the middle page of the converted PDF."
                )
            else:
                self.footer_hint_label.setText(
                    "Footer Y: load a main document to estimate the last footer line."
                )
                self.footer_hint_label.setToolTip("")
            return

        font_name = self.font_combo.currentText()
        font_size = float(self.font_size_spinbox.value())
        baseline_pt = self.pdf_processor.estimate_baseline_y_from_bottom_pt(
            hint.y_from_bottom_pt,
            font_name,
            font_size,
        )
        descent_pt = self.pdf_processor.estimate_font_descent_pt(font_name, font_size)
        baseline_cm = baseline_pt * (2.54 / 72.0)
        if hint.page_height_pt > 0:
            baseline_pct = (baseline_pt / hint.page_height_pt) * 100.0
        else:
            baseline_pct = 0.0

        text = (
            f"Footer baseline (page {hint.page_number}):\n"
            f"Y ≈ {baseline_cm:.2f} cm ({baseline_pct:.1f}%) from bottom"
        )
        self.footer_hint_label.setText(text)
        tip_parts = [
            "Glyph-box bottom of the last footer line, plus estimated descent "
            "for the selected page-number font/size (page numbers are drawn on "
            "the baseline).",
            f"Box bottom: {hint.y_from_bottom_cm:.2f} cm / {hint.y_from_bottom_percent:.1f}%",
            f"Descent ({font_name} {font_size:g} pt): {descent_pt:.2f} pt",
            f"Baseline Y: {baseline_cm:.2f} cm / {baseline_pct:.1f}% / {baseline_pt:.1f} pt",
        ]
        if hint.sample_text:
            tip_parts.append(f"Sample: “{hint.sample_text}”")
        self.footer_hint_label.setToolTip("\n".join(tip_parts))

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
            x_pct, y_pct, _, origin = POSITION_PRESETS[name]
            self._set_position_origin(origin)
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
            x_pct, y_pct, _, origin = POSITION_PRESETS[name]
            self._set_position_origin(origin)
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
        for segment in self.insertions:
            page_count = self.page_counts.get(segment.file_path, 0)
            total_pages += count_pages_in_spec(segment.pages_spec, page_count)
        return total_pages

    def _update_digits_minimum(self) -> None:
        total = self._get_total_page_count()
        min_digits = minimum_digits_for_page_count(total)
        self.digits_spinbox.setMinimum(min_digits)
        if self.digits_spinbox.value() < min_digits:
            self.digits_spinbox.setValue(min_digits)

    def _on_numbering_option_changed(self, *_args) -> None:
        self._update_numbering_preview()
        self._update_footer_hint_label()

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

    def _infer_text_anchor(self, x_percent: float, origin: Optional[str] = None) -> str:
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
            position_origin=self._position_origin,
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
        Create the file table section with insert locations.
        
        Returns:
            QGroupBox containing the file table
        """
        group = QGroupBox("Files to Insert")
        layout = QVBoxLayout()
        
        info_label = QLabel(
            "After the main document is loaded and its table of contents appears above, "
            "add the files you want to insert and choose where each one belongs. "
            "Use Split? to insert different page ranges of the same PDF at different places."
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
        self.file_table.setColumnCount(4)
        self.file_table.setHorizontalHeaderLabels(
            ["Split?", "File Name", "Pages", "Insert Location"]
        )
        
        header = self.file_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(80)
        
        self.file_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.file_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.file_table.setMinimumHeight(300)
        self.file_table.setWordWrap(False)
        
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

    def _create_toc_section(self) -> QGroupBox:
        """Create the table-of-contents display section."""
        group = QGroupBox("Table of Contents")
        layout = QVBoxLayout()

        self.toc_status_label = QLabel(
            "Select a main document to extract its table of contents."
        )
        self.toc_status_label.setWordWrap(True)
        layout.addWidget(self.toc_status_label)

        self.toc_table = QTableWidget()
        self.toc_table.setColumnCount(3)
        self.toc_table.setHorizontalHeaderLabels(
            ["Chapter Number", "Title", "Page"]
        )
        toc_header = self.toc_table.horizontalHeader()
        toc_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        toc_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        toc_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        toc_header.setMinimumSectionSize(50)
        self.toc_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.toc_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.toc_table.setMinimumHeight(180)
        self.toc_table.setWordWrap(False)
        layout.addWidget(self.toc_table)

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
    
    def _run_with_progress(
        self,
        title: str,
        label: str,
        work: Callable[[], Any],
    ) -> Any:
        """
        Show an indeterminate progress dialog while running work on the UI thread.

        Word COM conversion must stay on the main thread; the dialog appears first
        so the wait is visible instead of looking frozen.
        """
        progress = QProgressDialog(label, None, 0, 0, self)
        progress.setWindowTitle(title)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setCancelButton(None)
        progress.setRange(0, 0)
        progress.show()
        QApplication.processEvents()
        try:
            return work()
        finally:
            progress.close()
            QApplication.processEvents()

    def _select_main_document(self) -> None:
        """Select the main Word document and extract its table of contents."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Main Document",
            "",
            "Word Documents (*.docx *.doc)",
        )
        if not file_path:
            return

        suffix = Path(file_path).suffix.lower()
        if suffix not in {".docx", ".doc"}:
            QMessageBox.warning(
                self,
                "Invalid File",
                "The main document must be a Word file (.docx or .doc).",
            )
            return

        valid_files, invalid_files = self.file_handler.validate_files([file_path])
        if not valid_files:
            QMessageBox.warning(
                self,
                "Invalid File",
                invalid_files[0] if invalid_files else "The selected file is not supported.",
            )
            return

        if self.main_file_path and self.main_file_path != file_path and self.insertions:
            answer = QMessageBox.question(
                self,
                "Replace Main Document",
                "Changing the main document will clear the files to insert. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.insertions.clear()
            self.page_counts.clear()

        self.main_file_path = file_path
        self.toc_info = None
        self._toc_main_path = None
        self.main_file_label.setText(
            f"Main document: {self.file_handler.get_file_name(file_path)} (loading…)"
        )
        self.add_insertion_files_btn.setEnabled(False)
        self._update_status("Converting Word document with Microsoft Word…")

        def _load() -> Tuple[int, Any, Optional[FooterLineHint]]:
            # One Word launch: convert once (cached), then read pages + TOC from PDF.
            pdf_path = self.pdf_processor.convert_word_to_pdf(file_path)
            from PyPDF2 import PdfReader

            page_count = len(PdfReader(pdf_path, strict=False).pages)
            toc_info = extract_toc(file_path, self._resolve_pdf_for_toc)
            footer_hint = self.pdf_processor.detect_footer_last_line(pdf_path)
            return page_count, toc_info, footer_hint

        try:
            page_count, toc_info, footer_hint = self._run_with_progress(
                "Loading Word Document",
                "Converting with Microsoft Word and reading the table of contents…\n"
                "This can take a minute for large files.",
                _load,
            )
        except Exception as exc:
            self.main_file_path = None
            self._footer_hint = None
            self._update_footer_hint_label()
            self.main_file_label.setText(
                "No main document selected. Choose a Word file (.docx / .doc)."
            )
            self.add_insertion_files_btn.setEnabled(False)
            self._update_status("Ready")
            QMessageBox.critical(
                self,
                "Load Failed",
                f"Could not load the Word document:\n{exc}",
            )
            return

        self.page_counts[file_path] = page_count
        self.toc_info = toc_info
        self._toc_main_path = file_path
        self._footer_hint = footer_hint
        self._update_footer_hint_label()
        self.main_file_label.setText(
            f"Main document: {self.file_handler.get_file_name(file_path)} "
            f"({page_count} pages)"
        )
        self.add_insertion_files_btn.setEnabled(True)
        self._display_toc()
        self._refresh_table()
        self._update_total_pages()
        self._update_process_button_state()
        entry_count = len(toc_info.entries) if toc_info else 0
        if entry_count:
            self._update_status(f"Loaded {entry_count} table-of-contents entries")
        else:
            self._update_status("No table of contents found in main document")

    def _source_already_added(self, file_path: str) -> bool:
        return any(seg.file_path == file_path for seg in self.insertions)

    def _segments_for_file(self, file_path: str) -> List[InsertionSegment]:
        return [seg for seg in self.insertions if seg.file_path == file_path]

    def _find_segment(self, segment_id: str) -> Optional[InsertionSegment]:
        for seg in self.insertions:
            if seg.id == segment_id:
                return seg
        return None

    def _add_insertion_files(self) -> None:
        """Add one or more files that will be inserted into the main document."""
        if not self.main_file_path:
            QMessageBox.information(
                self,
                "Main Document Required",
                "Please select a main document before adding files to insert.",
            )
            return

        file_dialog = QFileDialog()
        file_dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
        file_dialog.setNameFilter("Documents (*.pdf *.docx *.doc)")

        if file_dialog.exec():
            selected_files = file_dialog.selectedFiles()
            valid_files, invalid_files = self.file_handler.validate_files(selected_files)

            added = 0
            for file_path in valid_files:
                if file_path == self.main_file_path:
                    continue
                if not self._source_already_added(file_path):
                    self._add_insertion_file_to_table(file_path)
                    added += 1

            if invalid_files:
                QMessageBox.warning(
                    self,
                    "Invalid Files",
                    "The following files were not added:\n" + "\n".join(invalid_files),
                )

            self._update_process_button_state()
            self._update_status(f"Added {added} file(s) to insert")

    def _add_insertion_file_to_table(self, file_path: str) -> None:
        """Add an insertion file as a single whole-file segment and refresh the table."""
        if self._source_already_added(file_path):
            return
        suffix = Path(file_path).suffix.lower()
        if suffix in {".docx", ".doc"}:
            page_count = self._run_with_progress(
                "Converting File",
                f"Converting {Path(file_path).name} with Microsoft Word…",
                lambda: self.pdf_processor.get_page_count(file_path),
            )
        else:
            page_count = self.pdf_processor.get_page_count(file_path)
        self.page_counts[file_path] = page_count
        self.insertions.append(InsertionSegment(file_path=file_path))
        self._refresh_table()
        self._update_total_pages()

    def _open_split_dialog(self, file_path: str) -> None:
        """Open the split dialog for a source file and rebuild its segments."""
        page_count = self.page_counts.get(file_path, 0)
        if page_count <= 0:
            page_count = self.pdf_processor.get_page_count(file_path, convert_word=True)
            self.page_counts[file_path] = page_count
        if page_count <= 0:
            QMessageBox.warning(
                self,
                "Cannot Split",
                "This file has no pages to split.",
            )
            return

        existing = self._segments_for_file(file_path)
        initial_groups = [
            seg.pages_spec or format_pages_display("", page_count)
            for seg in existing
        ]
        dialog = SplitPagesDialog(
            self.file_handler.get_file_name(file_path),
            page_count,
            initial_groups=initial_groups,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        groups = dialog.groups()
        self._apply_split_groups(file_path, groups, existing)
        self._refresh_table()
        self._update_total_pages()
        self._update_status(
            f"Split {self.file_handler.get_file_name(file_path)} into {len(groups)} group(s)"
        )

    def _apply_split_groups(
        self,
        file_path: str,
        groups: List[str],
        previous_segments: List[InsertionSegment],
    ) -> None:
        """Replace segments for file_path with new page groups, keeping table order."""
        new_segments: List[InsertionSegment] = []
        for index, pages_spec in enumerate(groups):
            prev = previous_segments[index] if index < len(previous_segments) else None
            new_segments.append(
                InsertionSegment(
                    file_path=file_path,
                    pages_spec=pages_spec,
                    insert_after=prev.insert_after if prev else 0,
                    use_custom_insert=prev.use_custom_insert if prev else False,
                )
            )

        rebuilt: List[InsertionSegment] = []
        placed = False
        for seg in self.insertions:
            if seg.file_path != file_path:
                rebuilt.append(seg)
                continue
            if not placed:
                rebuilt.extend(new_segments)
                placed = True
        if not placed:
            rebuilt.extend(new_segments)
        self.insertions = rebuilt

    def _refresh_table(self) -> None:
        """
        Rebuild the insertion-files table (with merged cells for split groups).
        """
        self.file_table.clearSpans()
        self.file_table.setRowCount(0)
        self._insert_controls.clear()

        main_page_count = 0
        if self.main_file_path:
            main_page_count = self.page_counts.get(self.main_file_path, 0)

        # Group contiguous segments by source file (splits stay contiguous).
        index = 0
        while index < len(self.insertions):
            file_path = self.insertions[index].file_path
            group: List[InsertionSegment] = []
            while (
                index < len(self.insertions)
                and self.insertions[index].file_path == file_path
            ):
                group.append(self.insertions[index])
                index += 1

            if file_path not in self.page_counts:
                self.page_counts[file_path] = self.pdf_processor.get_page_count(
                    file_path, convert_word=True
                )
            page_count = self.page_counts[file_path]
            start_row = self.file_table.rowCount()

            for offset, segment in enumerate(group):
                row = self.file_table.rowCount()
                self.file_table.insertRow(row)

                if offset == 0:
                    split_btn = QPushButton("split?")
                    split_btn.setToolTip(
                        "Split this PDF into page groups for different insert locations"
                    )
                    split_btn.clicked.connect(
                        lambda _checked=False, fp=file_path: self._open_split_dialog(fp)
                    )
                    self.file_table.setCellWidget(row, 0, split_btn)

                    name_item = QTableWidgetItem(
                        self.file_handler.get_file_name(file_path)
                    )
                    name_item.setData(Qt.ItemDataRole.UserRole, file_path)
                    name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.file_table.setItem(row, 1, name_item)
                else:
                    # Spanned cells still need placeholder items for selection.
                    self.file_table.setItem(row, 0, QTableWidgetItem(""))
                    self.file_table.setItem(row, 1, QTableWidgetItem(""))

                pages_item = QTableWidgetItem(
                    format_pages_display(segment.pages_spec, page_count)
                )
                pages_item.setData(Qt.ItemDataRole.UserRole, segment.id)
                pages_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                )
                pages_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.file_table.setItem(row, 2, pages_item)

                insert_widget = QWidget()
                insert_layout = QVBoxLayout()
                insert_layout.setContentsMargins(2, 2, 2, 2)
                insert_layout.setSpacing(2)

                insert_combo = prepare_combo_box(QComboBox(), 280)
                insert_combo.setSizeAdjustPolicy(
                    QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
                )
                insert_combo.setMinimumContentsLength(28)
                insert_combo.currentIndexChanged.connect(
                    lambda _i, sid=segment.id: self._on_insert_combo_changed(sid)
                )

                insert_spinbox = prepare_spinbox(QSpinBox(), 80)
                insert_spinbox.setMinimum(0)
                insert_spinbox.setMaximum(max(main_page_count, 0))
                insert_spinbox.setValue(segment.insert_after)
                insert_spinbox.setToolTip(
                    "Main-document page after which this segment is inserted "
                    "(0 = beginning)"
                )
                insert_spinbox.valueChanged.connect(
                    lambda value, sid=segment.id: self._update_insert_after_page(
                        sid, value
                    )
                )

                self._populate_insert_combo(insert_combo)
                insert_layout.addWidget(insert_combo)
                insert_layout.addWidget(insert_spinbox)
                insert_widget.setLayout(insert_layout)
                self._insert_controls[segment.id] = (insert_combo, insert_spinbox)
                self.file_table.setCellWidget(row, 3, insert_widget)
                self._sync_insert_controls(segment, insert_combo, insert_spinbox)

            rowspan = len(group)
            if rowspan > 1:
                self.file_table.setSpan(start_row, 0, rowspan, 1)
                self.file_table.setSpan(start_row, 1, rowspan, 1)

        self.file_table.resizeRowsToContents()
        self.file_table.resizeColumnToContents(2)
        self._update_total_pages()
    
    def _resolve_pdf_for_toc(self, file_path: str) -> str:
        """Return a PDF path for TOC extraction."""
        file_ext = Path(file_path).suffix.lower()
        if file_ext in [".docx", ".doc"]:
            return self.pdf_processor.convert_word_to_pdf(file_path)
        return file_path

    def _load_main_toc(self) -> None:
        """Extract and display the table of contents from the main document."""
        if not self.main_file_path:
            self.toc_info = None
            self._toc_main_path = None
            self._display_toc()
            return

        if self._toc_main_path == self.main_file_path and self.toc_info is not None:
            self._display_toc()
            return

        self._update_status("Extracting table of contents...")
        try:
            self.toc_info = extract_toc(
                self.main_file_path,
                self._resolve_pdf_for_toc,
            )
            self._toc_main_path = self.main_file_path
        except Exception as exc:
            self.toc_info = None
            self._toc_main_path = self.main_file_path
            self.toc_status_label.setText(
                f"Could not extract table of contents: {exc}"
            )
            self.toc_table.setRowCount(0)
            self._update_status("Ready")
            self._refresh_table()
            return

        self._display_toc()
        self._refresh_table()
        entry_count = len(self.toc_info.entries) if self.toc_info else 0
        if entry_count:
            self._update_status(f"Loaded {entry_count} table-of-contents entries")
        else:
            self._update_status("No table of contents found in main document")

    def _display_toc(self) -> None:
        """Populate the TOC table from extracted data."""
        self.toc_table.setRowCount(0)
        if not self.toc_info or not self.toc_info.entries:
            self.toc_status_label.setText(
                "No table of contents was found in the selected main document. "
                "Use the page number control when inserting files."
            )
            return

        source_label = self.toc_info.source.replace("_", " ")
        toc_page_note = ""
        if self.toc_info.has_toc_page:
            toc_start = self.toc_info.toc_page_index + 1
            toc_count = max(1, getattr(self.toc_info, "toc_page_count", 1) or 1)
            if toc_count > 1:
                toc_pages_text = f"pages {toc_start}-{toc_start + toc_count - 1}"
            else:
                toc_pages_text = f"page {toc_start}"
            if self.toc_info.can_update_in_place:
                toc_page_note = (
                    f" TOC detected at {toc_pages_text}. "
                    "Visible page numbers on all TOC pages will be updated to match "
                    "your stamped numbering, and chapter links will stay clickable."
                )
            else:
                toc_page_note = (
                    f" TOC detected at {toc_pages_text}, "
                    "but page-number positions could not be located for in-place update."
                )
        self.toc_status_label.setText(
            f"Extracted {len(self.toc_info.entries)} entries from {source_label}.{toc_page_note} "
            "Choose an insertion location for each additional file below."
        )

        for entry in self.toc_info.entries:
            row = self.toc_table.rowCount()
            self.toc_table.insertRow(row)

            chapter_item = QTableWidgetItem(entry.chapter_number_display)
            chapter_item.setFlags(Qt.ItemFlag.NoItemFlags)
            chapter_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.toc_table.setItem(row, 0, chapter_item)

            title_item = QTableWidgetItem(entry.chapter_title)
            title_item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.toc_table.setItem(row, 1, title_item)

            page_item = QTableWidgetItem(str(entry.page_number))
            page_item.setFlags(Qt.ItemFlag.NoItemFlags)
            page_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.toc_table.setItem(row, 2, page_item)

        self.toc_table.resizeColumnsToContents()
        # Keep the title column as the flexible one after content-based sizing.
        toc_header = self.toc_table.horizontalHeader()
        toc_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

    def _populate_insert_combo(self, combo: QComboBox) -> None:
        """Fill the insertion combo box with TOC-driven options."""
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Beginning (before page 1)", 0)

        if self.toc_info and self.toc_info.entries:
            main_pages = self.page_counts.get(self.main_file_path, 0)
            entries = self.toc_info.entries
            for index, entry in enumerate(entries):
                # After this chapter = last page of the chapter (page before next entry).
                if index + 1 < len(entries):
                    insert_page = max(
                        entry.page_number,
                        entries[index + 1].page_number - 1,
                    )
                else:
                    insert_page = max(entry.page_number, main_pages)
                label = entry.insert_location_label
                # Keep long titles readable in the closed combo and popup list.
                if len(label) > 80:
                    label = label[:77] + "..."
                combo.addItem(
                    f"After: {label} (after page {insert_page})",
                    insert_page,
                )

        combo.addItem("Custom page number...", -1)
        # Show many TOC entries at once (default Qt limit is only 10).
        combo.setMaxVisibleItems(max(12, min(40, combo.count())))
        view = combo.view()
        if view is not None:
            view.setTextElideMode(Qt.TextElideMode.ElideRight)
            # Widen the popup so full chapter titles are visible.
            metrics = combo.fontMetrics()
            widest = 280
            for i in range(combo.count()):
                widest = max(widest, metrics.horizontalAdvance(combo.itemText(i)) + 48)
            view.setMinimumWidth(min(widest, 720))
        combo.blockSignals(False)

    def _sync_insert_controls(
        self,
        segment: InsertionSegment,
        combo: QComboBox,
        spinbox: QSpinBox,
    ) -> None:
        """Align combo and spinbox with the stored insertion page."""
        spinbox.blockSignals(True)
        spinbox.setValue(segment.insert_after)
        spinbox.blockSignals(False)

        use_custom = segment.use_custom_insert
        if not self.toc_info or not self.toc_info.entries:
            combo.setVisible(False)
            spinbox.setEnabled(True)
            return

        combo.setVisible(True)
        matched_index = combo.count() - 1
        for index in range(combo.count()):
            if combo.itemData(index) == segment.insert_after and index != combo.count() - 1:
                matched_index = index
                use_custom = False
                break

        combo.blockSignals(True)
        combo.setCurrentIndex(matched_index)
        combo.blockSignals(False)

        custom_selected = use_custom or matched_index == combo.count() - 1
        segment.use_custom_insert = custom_selected
        spinbox.setEnabled(custom_selected if combo.isVisible() else True)

    def _on_insert_combo_changed(self, segment_id: str) -> None:
        """Apply a TOC-based insertion selection for a segment."""
        controls = self._insert_controls.get(segment_id)
        segment = self._find_segment(segment_id)
        if controls is None or segment is None:
            return

        combo, spinbox = controls
        selected_page = combo.currentData()
        if selected_page is None:
            return

        if int(selected_page) == -1:
            segment.use_custom_insert = True
            spinbox.setEnabled(True)
            return

        segment.use_custom_insert = False
        spinbox.blockSignals(True)
        spinbox.setValue(int(selected_page))
        spinbox.blockSignals(False)
        spinbox.setEnabled(False)
        segment.insert_after = int(selected_page)
        self._update_total_pages()

    def _update_insert_after_page(self, segment_id: str, value: int) -> None:
        """Update the insert-after-page value for a segment."""
        segment = self._find_segment(segment_id)
        if segment is None:
            return
        segment.insert_after = value
        segment.use_custom_insert = True
        controls = self._insert_controls.get(segment_id)
        if controls is not None:
            combo, _spinbox = controls
            if combo.isVisible() and combo.count() > 0:
                combo.blockSignals(True)
                combo.setCurrentIndex(combo.count() - 1)
                combo.blockSignals(False)
        self._update_total_pages()
    
    def _remove_selected_files(self) -> None:
        """Remove the selected segment rows from the table."""
        selected_rows = sorted(
            set(item.row() for item in self.file_table.selectedItems()),
            reverse=True,
        )
        # Also collect rows with selected cell widgets / current selection.
        for index in self.file_table.selectionModel().selectedRows():
            selected_rows.append(index.row())
        selected_rows = sorted(set(selected_rows), reverse=True)

        if not selected_rows:
            QMessageBox.information(
                self,
                "No Selection",
                "Please select one or more rows to remove.",
            )
            return

        ids_to_remove = set()
        for row in selected_rows:
            pages_item = self.file_table.item(row, 2)
            if pages_item is None:
                continue
            segment_id = pages_item.data(Qt.ItemDataRole.UserRole)
            if segment_id:
                ids_to_remove.add(segment_id)

        if not ids_to_remove:
            QMessageBox.information(
                self,
                "No Selection",
                "Please select one or more rows to remove.",
            )
            return

        self.insertions = [
            seg for seg in self.insertions if seg.id not in ids_to_remove
        ]
        # Drop page counts for sources that no longer appear.
        remaining_paths = {seg.file_path for seg in self.insertions}
        for path in list(self.page_counts.keys()):
            if path != self.main_file_path and path not in remaining_paths:
                del self.page_counts[path]

        self._refresh_table()
        self._update_total_pages()
        self._update_process_button_state()
        self._update_status(f"Removed {len(ids_to_remove)} row(s)")
    
    
    def _update_process_button_state(self) -> None:
        """
        Update the enabled state of the process button based on main document.
        """
        self.process_btn.setEnabled(self.main_file_path is not None)
    
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
        
        total_pages = self._get_total_page_count()
        self.total_pages_label.setText(f"Total Pages: {total_pages}")
        self._update_digits_minimum()
        self._update_numbering_preview()
    
    def _get_insertions(self) -> List[Tuple[str, int, str]]:
        """
        Get insertion segments with page positions and page specs.
        
        Returns:
            List of (file_path, after_page, pages_spec) tuples
        """
        return [
            (seg.file_path, seg.insert_after, seg.pages_spec)
            for seg in self.insertions
        ]
    
    def _process_files(self) -> None:
        """
        Process files: assemble around main document, merge, and add page numbers.
        """
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
            
            insertions = self._get_insertions()
            from pathlib import Path
            pre_converted_pdfs: Dict[str, str] = {}
            
            self._update_status("Converting Word files to PDF...")
            paths_to_convert = [self.main_file_path] + [
                path for path, _after, _spec in insertions
            ]
            # Unique while preserving order
            seen_paths = set()
            unique_paths: List[str] = []
            for path in paths_to_convert:
                if path not in seen_paths:
                    seen_paths.add(path)
                    unique_paths.append(path)
            for file_path in unique_paths:
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

            self.pdf_processor.process_files_with_main(
                self.main_file_path,
                insertions,
                {},
                output_path,
                start_page_number=1,
                pre_converted_pdfs=pre_converted_pdfs,
                page_number_settings=page_settings,
                toc_info=self.toc_info,
            )

            toc_note = ""
            if self.toc_info and self.toc_info.entries and not self.toc_info.can_update_in_place:
                toc_note = (
                    "\n\nNote: TOC chapter links were preserved, but visible TOC page "
                    "numbers could not be updated automatically. Check that they match "
                    "the stamped page numbers."
                )

            self._update_status(f"Success! Combined PDF saved to: {output_path}")
            self._show_success_dialog(output_path, toc_note)
            
        except Exception as e:
            self._update_status(f"Error: {str(e)}")
            QMessageBox.critical(
                self,
                "Error",
                f"An error occurred while processing files:\n{str(e)}"
            )
        finally:
            self.process_btn.setEnabled(True)

    def _show_success_dialog(self, output_path: str, extra_note: str = "") -> None:
        """Show success message with option to open the generated PDF."""
        msg = QMessageBox(self)
        msg.setWindowTitle("Success")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(
            "Files have been combined and page numbers added. "
            "TOC chapter links and hierarchical bookmarks are included in the output PDF."
        )
        info = f"Saved to:\n{output_path}"
        if extra_note:
            info += extra_note
        msg.setInformativeText(info)
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
                "main_file_path": self.main_file_path,
                "insertions": [seg.to_dict() for seg in self.insertions],
                # Legacy keys kept for older readers; prefer "insertions".
                "file_paths": list(
                    dict.fromkeys(seg.file_path for seg in self.insertions)
                ),
                "insert_after_page": {
                    seg.file_path: seg.insert_after for seg in self.insertions
                },
                "buffer_pages": {},
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
                "position_origin": self._position_origin,
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

        self.insertions.clear()
        self.page_counts.clear()
        self.main_file_path = None
        self.toc_info = None
        self._toc_main_path = None
        self._footer_hint = None
        self._update_footer_hint_label()

        missing: List[str] = []
        main_path = files.get("main_file_path")
        if main_path and Path(main_path).is_file():
            if Path(main_path).suffix.lower() not in {".docx", ".doc"}:
                missing.append(main_path)
                self.main_file_label.setText(
                    "No main document selected. Choose a Word file (.docx / .doc)."
                )
                self.add_insertion_files_btn.setEnabled(False)
            else:
                self.main_file_path = main_path

                def _load_main() -> Tuple[int, Any, Optional[FooterLineHint]]:
                    pdf_path = self.pdf_processor.convert_word_to_pdf(main_path)
                    from PyPDF2 import PdfReader

                    page_count = len(PdfReader(pdf_path, strict=False).pages)
                    toc_info = extract_toc(main_path, self._resolve_pdf_for_toc)
                    footer_hint = self.pdf_processor.detect_footer_last_line(pdf_path)
                    return page_count, toc_info, footer_hint

                try:
                    page_count, toc_info, footer_hint = self._run_with_progress(
                        "Loading Session",
                        "Converting main Word document and reading the table of contents…",
                        _load_main,
                    )
                except Exception as exc:
                    missing.append(main_path)
                    self.main_file_path = None
                    self._footer_hint = None
                    self._update_footer_hint_label()
                    self.main_file_label.setText(
                        "No main document selected. Choose a Word file (.docx / .doc)."
                    )
                    self.add_insertion_files_btn.setEnabled(False)
                    QMessageBox.warning(
                        self,
                        "Main Document Failed",
                        f"Could not load the session main document:\n{exc}",
                    )
                else:
                    self.page_counts[main_path] = page_count
                    self.toc_info = toc_info
                    self._toc_main_path = main_path
                    self._footer_hint = footer_hint
                    self._update_footer_hint_label()
                    self.main_file_label.setText(
                        f"Main document: {self.file_handler.get_file_name(main_path)} "
                        f"({page_count} pages)"
                    )
                    self.add_insertion_files_btn.setEnabled(True)
                    self._display_toc()
        else:
            if main_path:
                missing.append(main_path)
            self.main_file_label.setText(
                "No main document selected. Choose a Word file (.docx / .doc)."
            )
            self.add_insertion_files_btn.setEnabled(False)

        saved_insertions = files.get("insertions")
        if isinstance(saved_insertions, list) and saved_insertions:
            for raw in saved_insertions:
                if not isinstance(raw, dict):
                    continue
                file_path = str(raw.get("file_path", ""))
                if file_path == main_path:
                    continue
                if not file_path or not Path(file_path).is_file():
                    if file_path:
                        missing.append(file_path)
                    continue
                if file_path not in self.page_counts:
                    try:
                        suffix = Path(file_path).suffix.lower()
                        if suffix in {".docx", ".doc"}:
                            page_count = self._run_with_progress(
                                "Converting File",
                                f"Converting {Path(file_path).name} with Microsoft Word…",
                                lambda fp=file_path: self.pdf_processor.get_page_count(fp),
                            )
                        else:
                            page_count = self.pdf_processor.get_page_count(file_path)
                        self.page_counts[file_path] = page_count
                    except Exception:
                        missing.append(file_path)
                        continue
                self.insertions.append(InsertionSegment.from_dict(raw))
        else:
            # Legacy session format: one whole-file segment per path.
            saved_paths: List[str] = files.get("file_paths", [])
            legacy_insert = files.get("insert_after_page", {})
            for file_path in saved_paths:
                if file_path == main_path:
                    continue
                if Path(file_path).is_file():
                    self._add_insertion_file_to_table(file_path)
                    if file_path in legacy_insert and self.insertions:
                        # _add appends one segment for this file
                        for seg in reversed(self.insertions):
                            if seg.file_path == file_path:
                                seg.insert_after = int(legacy_insert[file_path])
                                break
                else:
                    missing.append(file_path)

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

        # Named presets use current corner-relative defaults; Custom keeps saved offsets.
        if pos_name == CUSTOM_POSITION or pos_name not in POSITION_PRESETS:
            saved_origin = numbering.get("position_origin", DEFAULT_ORIGIN)
            self._set_position_origin(str(saved_origin))
            self.x_slider.setValue(int(numbering.get("x_percent", 50)))
            self.y_slider.setValue(int(numbering.get("y_percent", 5)))
            self.x_cm_spinbox.setValue(float(numbering.get("x_cm", A4_WIDTH_CM * 0.5)))
            self.y_cm_spinbox.setValue(float(numbering.get("y_cm", A4_HEIGHT_CM * 0.05)))
        elif pos_name in POSITION_PRESETS:
            # Re-apply preset so origin and default margins match the current model.
            self._apply_position_preset(pos_name)

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

