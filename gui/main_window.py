"""
Main window module for The Reportinator application.
Contains the PyQt6 UI components and main application logic.
"""

from typing import List, Optional, Dict, Tuple, Any, Callable
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QFileDialog, QLabel, QSpinBox, QMessageBox, QGroupBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QRadioButton,
    QButtonGroup, QCheckBox, QLineEdit, QComboBox,
    QDoubleSpinBox, QGridLayout, QFrame, QSizePolicy, QScrollArea,
    QProgressDialog, QApplication, QDialog, QTabWidget,
)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QColor, QFont, QDesktopServices
from pathlib import Path
from datetime import datetime
import json
import sys

from backend.file_handler import FileHandler
from backend.pdf_processor import PDFProcessor, FooterLineHint
from backend.page_spec import (
    InsertionSegment,
    count_pages_in_spec,
    format_pages_display,
)
from gui.split_dialog import SplitPagesDialog
from backend.page_number_config import (
    PageNumberSettings,
    load_font_names,
    minimum_digits_for_page_count,
    DEFAULT_FONT,
    DEFAULT_FONT_SIZE,
    DEFAULT_SEPARATOR,
)
from backend.page_geometry import (
    PageType,
    PageTypeInfo,
    page_type_from_key,
    summarize_page_types,
)
from backend.page_spec import parse_pages_spec

from gui.position_panel import (
    A4_HEIGHT_CM,
    A4_WIDTH_CM,
    PositionSettingsPanel,
)
from gui.swapper_tab import SwapperTab
from gui.ui_helpers import (
    add_form_row,
    configure_compact_grid,
    prepare_line_edit,
    prepare_spinbox,
    prepare_double_spinbox,
    prepare_combo_box,
    FIELD_MIN_HEIGHT,
)
from backend.session_manager import (
    session_directory,
    build_session_filename,
    save_session,
    load_session,
    inserter_section,
    swapper_section,
    windows_username,
    primary_action_button_style,
)
from backend.toc_handler import TocInfo, extract_toc

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
        self._footer_hint: Optional[FooterLineHint] = None
        self._current_session_path: Optional[Path] = initial_session_path
        # Detected page sizes/orientations across the assembled document, and one
        # position panel per detected type (a single type means no tabs).
        self._page_types: List[PageTypeInfo] = []
        self._position_panels: Dict[PageType, PositionSettingsPanel] = {}
        self._single_position_panel: Optional[PositionSettingsPanel] = None
        self._page_dimension_cache: Dict[str, List[Tuple[float, float, int]]] = {}
        
        self.setWindowTitle("The Reportinator")
        self.setGeometry(100, 100, 1100, 820)
        
        self._setup_ui()
        self._apply_lime_green_styling()
        if initial_session_path is not None:
            self._load_session_file(initial_session_path)
    
    def _setup_ui(self) -> None:
        """
        Set up the user interface components.
        """
        tabs = QTabWidget()
        tabs.addTab(self._create_inserter_tab(), "Insert 📥")
        self.swapper_tab = SwapperTab(self)
        tabs.addTab(self.swapper_tab, "Swap 🔄️ / Add ➕ ")
        self.setCentralWidget(tabs)

    def _create_inserter_tab(self) -> QWidget:
        """Build the original insert / page-numbering workflow as a tab page."""
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
        return scroll
    
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

        location_block = QHBoxLayout()
        location_block.setSpacing(12)

        # One position panel per detected page type. Panels (and the tab bar that
        # holds them when there is more than one) are built by
        # _rebuild_position_panels once page sizes are known.
        self.position_panels_container = QWidget()
        self._position_panels_layout = QVBoxLayout(self.position_panels_container)
        self._position_panels_layout.setContentsMargins(0, 0, 0, 0)
        self._position_panels_layout.setSpacing(0)
        self.position_tabs: Optional[QTabWidget] = None
        location_block.addWidget(self.position_panels_container, stretch=1)

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
        self.apply_footer_y_btn = QPushButton("Use bottom footer Y")
        self.apply_footer_y_btn.setToolTip(
            "Set absolute Y to the baseline of the lowest detected footer line."
        )
        self.apply_footer_y_btn.setEnabled(False)
        self.apply_footer_y_btn.clicked.connect(self._apply_footer_hint_y)
        footer_hint_col = QVBoxLayout()
        footer_hint_col.setSpacing(4)
        footer_hint_col.setContentsMargins(0, 0, 0, 0)
        footer_hint_col.addWidget(self.footer_hint_label)
        footer_hint_col.addWidget(self.apply_footer_y_btn)
        footer_hint_col.addStretch()
        location_block.addLayout(footer_hint_col)

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
        self._rebuild_position_panels()
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

    # ------------------------------------------------- page types and panels

    def _document_page_dimensions(self) -> List[Tuple[float, float, int]]:
        """
        Collect (width, height, rotation) for every page of the assembled output.

        Insert files contribute only the pages their split spec selects, so a
        page size that is present in a source PDF but never inserted does not
        create a tab nobody needs.
        """
        dimensions: List[Tuple[float, float, int]] = []
        if not self.main_file_path:
            return dimensions

        dimensions.extend(self._file_page_dimensions(self.main_file_path))
        for segment in self.insertions:
            page_dims = self._file_page_dimensions(segment.file_path)
            if not page_dims:
                continue
            spec = (segment.pages_spec or "").strip()
            if not spec:
                dimensions.extend(page_dims)
                continue
            for page_number in parse_pages_spec(spec, len(page_dims)):
                if 1 <= page_number <= len(page_dims):
                    dimensions.append(page_dims[page_number - 1])
        return dimensions

    def _file_page_dimensions(self, file_path: str) -> List[Tuple[float, float, int]]:
        """Page dimensions for one source file, cached per path."""
        cached = self._page_dimension_cache.get(file_path)
        if cached is not None:
            return cached
        dimensions = self.pdf_processor.page_dimensions(file_path)
        self._page_dimension_cache[file_path] = dimensions
        return dimensions

    def _refresh_page_types(self) -> None:
        """Re-detect page types from the current file set and rebuild the panels."""
        self._page_types = summarize_page_types(self._document_page_dimensions())
        self._rebuild_position_panels()

    def _rebuild_position_panels(self) -> None:
        """
        Show one position panel per detected page type.

        A single page type (or none detected yet) keeps the plain inline layout;
        two or more add a tab per type. Settings already entered for a type are
        carried over so re-detection does not discard the user's work.
        """
        saved = {
            page_type: panel.to_dict()
            for page_type, panel in self._position_panels.items()
        }
        active_label = ""
        if self.position_tabs is not None and self.position_tabs.count():
            active_label = self.position_tabs.tabText(self.position_tabs.currentIndex())

        self._clear_position_panels()

        page_types = self._page_types
        if len(page_types) <= 1:
            info = page_types[0] if page_types else None
            panel = self._create_position_panel(info, saved)
            self._position_panels_layout.addWidget(panel)
            if info is not None:
                self._position_panels[info.page_type] = panel
                panel.set_page_size_caption(
                    f"{info.label} — {info.width_cm:.1f} × {info.height_cm:.1f} cm"
                )
            self._single_position_panel = panel
            self._update_numbering_preview()
            return

        self._single_position_panel = None
        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        for info in page_types:
            panel = self._create_position_panel(info, saved)
            panel.set_page_size_caption(
                f"{info.width_cm:.1f} × {info.height_cm:.1f} cm — "
                f"{info.page_count} page(s)"
            )
            self._position_panels[info.page_type] = panel
            tabs.addTab(panel, info.label)
        index = 0
        for tab_index in range(tabs.count()):
            if tabs.tabText(tab_index) == active_label:
                index = tab_index
                break
        tabs.setCurrentIndex(index)
        tabs.currentChanged.connect(lambda _index: self._on_numbering_option_changed())
        self.position_tabs = tabs
        self._position_panels_layout.addWidget(tabs)
        self._update_numbering_preview()

    def _create_position_panel(
        self,
        info: Optional[PageTypeInfo],
        saved: Dict[PageType, Dict[str, Any]],
    ) -> PositionSettingsPanel:
        """Build a panel for one page type, restoring any settings it already had."""
        if info is not None:
            panel = PositionSettingsPanel(info.width_cm, info.height_cm)
        else:
            panel = PositionSettingsPanel(A4_WIDTH_CM, A4_HEIGHT_CM)

        previous = saved.get(info.page_type) if info is not None else None
        if previous is None and len(saved) == 1:
            # Carrying the only existing configuration forward keeps a freshly
            # detected type from resetting what the user just set up.
            previous = next(iter(saved.values()))
        if previous is not None:
            panel.load_dict(previous)

        panel.changed.connect(self._on_numbering_option_changed)
        return panel

    def _clear_position_panels(self) -> None:
        """Detach and delete the current panels/tab bar."""
        self._position_panels.clear()
        self.position_tabs = None
        self._single_position_panel = None
        while self._position_panels_layout.count():
            item = self._position_panels_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _active_position_panel(self) -> PositionSettingsPanel:
        """The panel the user is currently looking at (drives the live preview)."""
        if self.position_tabs is not None:
            current = self.position_tabs.currentWidget()
            if isinstance(current, PositionSettingsPanel):
                return current
        if self._single_position_panel is None:
            # Panels are built during UI setup; this only guards earlier access.
            self._single_position_panel = PositionSettingsPanel(
                A4_WIDTH_CM, A4_HEIGHT_CM
            )
        return self._single_position_panel

    def _position_panels_in_order(self) -> List[PositionSettingsPanel]:
        """Every position panel currently shown."""
        if self.position_tabs is not None:
            return [
                widget
                for widget in (
                    self.position_tabs.widget(index)
                    for index in range(self.position_tabs.count())
                )
                if isinstance(widget, PositionSettingsPanel)
            ]
        return [self._active_position_panel()]

    def _footer_baseline_from_hint(self) -> Optional[Tuple[float, float, float]]:
        """
        Baseline Y for the detected footer line.

        Returns (baseline_pt, baseline_cm, baseline_pct) or None.
        ``detect_footer_last_line`` already returns a baseline estimate.
        """
        hint = self._footer_hint
        if hint is None:
            return None
        baseline_pt = float(hint.y_from_bottom_pt)
        baseline_cm = baseline_pt * (2.54 / 72.0)
        if hint.page_height_pt > 0:
            baseline_pct = (baseline_pt / hint.page_height_pt) * 100.0
        else:
            baseline_pct = 0.0
        return baseline_pt, baseline_cm, baseline_pct

    def _update_footer_hint_label(self) -> None:
        """Refresh the footer Y hint shown beside the position diagram."""
        hint = self._footer_hint
        if hint is None:
            self.apply_footer_y_btn.setEnabled(False)
            if self.main_file_path:
                self.footer_hint_label.setText(
                    "Footer Y: no footer text found near the middle of the document."
                )
                self.footer_hint_label.setToolTip(
                    "Checked the bottom band of several pages around the middle "
                    "of the converted PDF."
                )
            else:
                self.footer_hint_label.setText(
                    "Footer Y: load a main document to estimate the last footer line."
                )
                self.footer_hint_label.setToolTip("")
            return

        baseline = self._footer_baseline_from_hint()
        if baseline is None:
            self.apply_footer_y_btn.setEnabled(False)
            return
        baseline_pt, baseline_cm, baseline_pct = baseline

        sample_line = ""
        if hint.sample_text:
            sample_line = f"\n“{hint.sample_text}”"
        text = (
            f"Bottom footer line (page {hint.page_number}):\n"
            f"Y ≈ {baseline_cm:.2f} cm ({baseline_pct:.1f}%) from bottom"
            f"{sample_line}"
        )
        self.footer_hint_label.setText(text)
        tip_parts = [
            "Estimated baseline of the lowest footer line (median across pages "
            "near the middle). Use this as the stamp Y so page numbers sit on "
            "that line.",
            f"Baseline Y: {baseline_cm:.2f} cm / {baseline_pct:.1f}% / {baseline_pt:.1f} pt",
        ]
        if len(self._page_types) > 1:
            tip_parts.append(
                "Sampled from the main document; applies to the selected page-type tab."
            )
        if hint.sample_text:
            tip_parts.append(f"Sample: “{hint.sample_text}”")
        self.footer_hint_label.setToolTip("\n".join(tip_parts))
        self.apply_footer_y_btn.setEnabled(True)

    def _apply_footer_hint_y(self) -> None:
        """Apply the detected bottom-footer baseline to the visible page type."""
        baseline = self._footer_baseline_from_hint()
        if baseline is None:
            return
        _, baseline_cm, _ = baseline
        self._active_position_panel().apply_absolute_y_cm(baseline_cm)

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

    def _resolve_label_text(self) -> str:
        if self.seite_radio.isChecked():
            return "Seite"
        if self.custom_label_radio.isChecked():
            return self.custom_label_edit.text().strip() or "Custom"
        return "Page"

    def _settings_for_panel(
        self, panel: PositionSettingsPanel
    ) -> PageNumberSettings:
        """Combine the shared numbering options with one panel's position."""
        position = panel.values()
        sep = self.separator_edit.text()
        if not sep:
            sep = DEFAULT_SEPARATOR

        return PageNumberSettings(
            use_label=self.page_seite_checkbox.isChecked(),
            label_text=self._resolve_label_text(),
            chapter_prefix=self.chapter_prefix_edit.text(),
            num_digits=self.digits_spinbox.value(),
            separator=sep,
            position_name=position.position_name,
            position_mode=position.position_mode,
            position_origin=position.position_origin,
            x_percent=position.x_percent,
            y_percent=position.y_percent,
            x_cm=position.x_cm,
            y_cm=position.y_cm,
            text_anchor=position.text_anchor,
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

    def _get_page_number_settings(self) -> PageNumberSettings:
        """
        Settings for the page type currently on screen.

        Also used as the fallback for page types that were not detected before
        processing (for example a size introduced by a late file swap).
        """
        return self._settings_for_panel(self._active_position_panel())

    def _get_settings_by_page_type(self) -> Dict[PageType, PageNumberSettings]:
        """Per-page-type settings for stamping; empty when only one type exists."""
        if len(self._position_panels) <= 1:
            return {}
        return {
            page_type: self._settings_for_panel(panel)
            for page_type, panel in self._position_panels.items()
        }

    def _restore_position_settings(self, numbering: Dict[str, Any]) -> None:
        """
        Fill the position panels from a saved session.

        Sessions saved before per-page-type positions existed only have the flat
        position keys; those are applied to every panel so the old placement is
        preserved for each detected size.
        """
        by_page_type = numbering.get("position_by_page_type")
        if not isinstance(by_page_type, dict):
            by_page_type = {}

        saved_panels: Dict[PageType, Dict[str, Any]] = {}
        for key, payload in by_page_type.items():
            page_type = page_type_from_key(str(key))
            if page_type is not None and isinstance(payload, dict):
                saved_panels[page_type] = payload

        for page_type, panel in self._position_panels.items():
            panel.load_dict(saved_panels.get(page_type, numbering))

        if not self._position_panels:
            self._active_position_panel().load_dict(numbering)

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

        self.save_session_btn = QPushButton("Save Session")
        self.save_session_btn.clicked.connect(self._save_session)
        self.save_session_btn.setMinimumHeight(50)
        self.save_session_btn.setMinimumWidth(250)
        layout.addWidget(self.save_session_btn)
        
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
        primary = primary_action_button_style(lime_green_hex)
        self.process_btn.setStyleSheet(primary)
        self.save_session_btn.setStyleSheet(primary)
        if hasattr(self, "swapper_tab") and self.swapper_tab is not None:
            self.swapper_tab.apply_primary_button_style(primary)

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
            self._page_dimension_cache.clear()

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
        self._refresh_page_types()
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

            self._refresh_page_types()
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
        self._refresh_page_types()
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

            page_item = QTableWidgetItem(entry.display_page_label)
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
        for path in list(self._page_dimension_cache.keys()):
            if path != self.main_file_path and path not in remaining_paths:
                del self._page_dimension_cache[path]

        self._refresh_table()
        self._update_total_pages()
        self._refresh_page_types()
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
            settings_by_page_type = self._get_settings_by_page_type()

            self.pdf_processor.process_files_with_main(
                self.main_file_path,
                insertions,
                {},
                output_path,
                start_page_number=1,
                pre_converted_pdfs=pre_converted_pdfs,
                page_number_settings=page_settings,
                toc_info=self.toc_info,
                settings_by_page_type=settings_by_page_type,
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

    def _export_inserter_session_data(self) -> Dict[str, Any]:
        active_position = self._active_position_panel().to_dict()
        return {
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
                # Flat position keys describe the visible page type, so older
                # builds of the app can still read a usable position.
                **active_position,
                # One entry per detected page size/orientation.
                "position_by_page_type": {
                    page_type.key: panel.to_dict()
                    for page_type, panel in self._position_panels.items()
                },
                "font_name": self.font_combo.currentText(),
                "font_size": self.font_size_spinbox.value(),
                "font_color_rgb": [
                    self.color_r_spin.value(),
                    self.color_g_spin.value(),
                    self.color_b_spin.value(),
                ],
            },
        }

    def _export_session_data(self) -> Dict[str, Any]:
        """Full session payload with separate Insert and Swap tab sections."""
        swapper_data: Dict[str, Any] = {}
        if hasattr(self, "swapper_tab") and self.swapper_tab is not None:
            swapper_data = self.swapper_tab.export_session_data()
        return {
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "username": windows_username(),
            "inserter": self._export_inserter_session_data(),
            "swapper": swapper_data,
        }

    def _save_session(self) -> None:
        """Save Insert + Swap tab state to the same session JSON file."""
        try:
            folder = session_directory()
            if self._current_session_path is not None:
                suggested = str(self._current_session_path)
            else:
                main_for_name = self.main_file_path
                if not main_for_name and hasattr(self, "swapper_tab"):
                    main_for_name = self.swapper_tab.main_file_path
                suggested = str(folder / build_session_filename(main_for_name))

            chosen, _ = QFileDialog.getSaveFileName(
                self,
                "Save Session",
                suggested,
                "Session Files (*.json)",
            )
            if not chosen:
                return

            path = Path(chosen)
            if path.suffix.lower() != ".json":
                path = path.with_suffix(".json")

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
        self._apply_session_data(inserter_section(data))
        if hasattr(self, "swapper_tab") and self.swapper_tab is not None:
            self.swapper_tab.apply_session_data(swapper_section(data))
        self._current_session_path = path
        self._update_status(f"Loaded session: {path.name}")

    def _apply_session_data(self, data: Dict[str, Any]) -> None:
        files = data.get("files", {})
        numbering = data.get("numbering", {})
        if not isinstance(files, dict):
            files = {}
        if not isinstance(numbering, dict):
            numbering = {}

        self.insertions.clear()
        self.page_counts.clear()
        self._page_dimension_cache.clear()
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

        font_name = str(numbering.get("font_name", DEFAULT_FONT))
        if self.font_combo.findText(font_name) >= 0:
            self.font_combo.setCurrentText(font_name)
        self.font_size_spinbox.setValue(float(numbering.get("font_size", DEFAULT_FONT_SIZE)))

        rgb = numbering.get("font_color_rgb", [0, 0, 0])
        if isinstance(rgb, (list, tuple)) and len(rgb) >= 3:
            self.color_r_spin.setValue(int(rgb[0]))
            self.color_g_spin.setValue(int(rgb[1]))
            self.color_b_spin.setValue(int(rgb[2]))

        # Detect page types from the restored files first, then fill each panel.
        self._refresh_page_types()
        self._restore_position_settings(numbering)
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

