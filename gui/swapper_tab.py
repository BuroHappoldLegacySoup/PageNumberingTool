"""
Swapper tab: replace selected pages in a main PDF with pages from other PDFs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QEvent, Qt, QUrl
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from backend.file_handler import FileHandler
from backend.page_number_config import PageNumberSettings
from backend.page_swapper import (
    DEFAULT_PAGE_NUMBER_POSITION,
    PageSwapper,
    SwapOperation,
    example_text_from_numbering,
    extract_page_number_base_text,
    labels_for_multi_swap,
    normalize_swap_letter,
    page_number_settings_from_session,
    position_key_from_name,
)
from backend.page_spec import (
    InsertionSegment,
    count_pages_in_spec,
    format_pages_display,
    parse_pages_spec,
)
from backend.pdf_processor import PDFProcessor
from backend.session_manager import (
    list_session_files,
    load_session,
    numbering_from_session,
    session_directory,
)
from backend.toc_handler import TocInfo, extract_toc
from gui.split_dialog import SplitPagesDialog
from gui.ui_helpers import prepare_combo_box, prepare_line_edit, prepare_spinbox, FIELD_MIN_HEIGHT

LIME_GREEN = QColor(206, 220, 0)
DEFAULT_TOC_FONT_RGB = (220, 20, 60)
READONLY_TEXT = QColor(128, 128, 128)

# Relative widths for tables that fill the viewport with fixed proportions.
OPS_COLUMN_RATIOS = (12, 18, 24, 8, 12, 26)
INSERT_COLUMN_RATIOS = (10, 28, 14, 48)


class SwapOutputOptionsDialog(QDialog):
    """Choose which PDF outputs to create after processing swaps/adds."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Output Options")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Choose which PDF(s) to save. You can select one or both options."
            )
        )

        self.entire_cb = QCheckBox("Entire document")
        self.entire_cb.setChecked(True)
        self.entire_cb.setToolTip(
            "Save the full updated PDF (all original pages with swaps and inserts applied)."
        )
        layout.addWidget(self.entire_cb)

        self.toc_new_cb = QCheckBox("Only TOC + New pages")
        self.toc_new_cb.setToolTip(
            "Save a shorter PDF with the updated TOC sheets plus swapped and added pages only."
        )
        layout.addWidget(self.toc_new_cb)

        note = QLabel(
            "“Only TOC + New pages” includes the updated table of contents "
            "(when detected) and every swapped or inserted page."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        if not self.entire_cb.isChecked() and not self.toc_new_cb.isChecked():
            QMessageBox.warning(
                self,
                "No output selected",
                "Select at least one output option.",
            )
            return
        self.accept()

    def want_entire(self) -> bool:
        return self.entire_cb.isChecked()

    def want_toc_new(self) -> bool:
        return self.toc_new_cb.isChecked()


class SwapperTab(QWidget):
    """UI for swapping pages out of a main PDF and stamping letter / -i labels."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.file_handler = FileHandler()
        self.pdf_processor = PDFProcessor()
        self.page_swapper = PageSwapper(self.pdf_processor)

        self.main_file_path: Optional[str] = None
        self.main_page_count: int = 0
        # Physical 1-based page -> stamp text (Method A)
        self.page_number_texts: Dict[int, str] = {}
        self.page_number_position: str = DEFAULT_PAGE_NUMBER_POSITION
        self.stamp_settings: Optional[PageNumberSettings] = None
        self.session_example_text: str = ""
        self.session_path: Optional[str] = None
        self.source_page_counts: Dict[str, int] = {}
        self.operations: List[SwapOperation] = []
        self.insertions: List[InsertionSegment] = []
        self.toc_info: Optional[TocInfo] = None
        self._insert_controls: Dict[str, Tuple[QComboBox, QSpinBox]] = {}

        self._setup_ui()
        self._apply_styling()
        self._on_style_source_changed()

    def _setup_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        content = QWidget()
        content.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout = QVBoxLayout(content)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        layout.addWidget(self._create_main_section(), 0)
        layout.addWidget(self._create_style_section(), 0)
        layout.addWidget(self._create_additional_settings_section(), 0)
        layout.addWidget(self._create_ops_section(), 1)
        layout.addWidget(self._create_add_pages_section(), 1)

        btn_row = QHBoxLayout()
        self.process_btn = QPushButton("Process & Save PDF")
        self.process_btn.setEnabled(False)
        self.process_btn.setMinimumHeight(50)
        self.process_btn.setMinimumWidth(250)
        self.process_btn.clicked.connect(self._process)
        btn_row.addWidget(self.process_btn)
        btn_row.addStretch()
        self.save_session_btn = QPushButton("Save Session")
        self.save_session_btn.setMinimumHeight(50)
        self.save_session_btn.setMinimumWidth(250)
        self.save_session_btn.clicked.connect(self._save_session)
        btn_row.addWidget(self.save_session_btn)
        layout.addLayout(btn_row, 0)

        self.status_label = QLabel("Ready — select a main PDF to begin.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label, 0)

        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _create_main_section(self) -> QGroupBox:
        group = QGroupBox("Main Document (PDF)")
        layout = QVBoxLayout()
        row = QHBoxLayout()
        select_btn = QPushButton("Select Main PDF")
        select_btn.clicked.connect(self._select_main)
        row.addWidget(select_btn)
        row.addStretch()
        layout.addLayout(row)

        self.main_label = QLabel("No main PDF selected.")
        self.main_label.setWordWrap(True)
        layout.addWidget(self.main_label)
        hint = QLabel(
            "Use <b>Swap Pages</b> to replace selected main pages, or <b>Add Pages</b> "
            "to insert PDFs after a page without replacing anything. "
            "Inserted pages keep the document numbering style "
            "(e.g. after page 30 → <b>30-1</b>, <b>30-2</b>…). "
            "Matching swapped pages get the original stamp + your letter; "
            "extra replacement pages get <b>-1</b>, <b>-2</b>… "
            "TOC lines at changed pages are updated in the picked font colour; "
            "bookmarks and links are remapped on save."
        )
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(hint)
        group.setLayout(layout)
        return group

    def _create_style_section(self) -> QGroupBox:
        group = QGroupBox("Existing page number style")
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        source_row = QHBoxLayout()
        source_row.setSpacing(8)
        self.manual_style_radio = QRadioButton("Define manually")
        self.session_style_radio = QRadioButton("Load from Inserter session")
        self.manual_style_radio.setChecked(True)
        self._style_source_group = QButtonGroup(self)
        self._style_source_group.addButton(self.manual_style_radio)
        self._style_source_group.addButton(self.session_style_radio)
        self.manual_style_radio.toggled.connect(self._on_style_source_changed)
        self.session_style_radio.toggled.connect(self._on_style_source_changed)
        source_row.addWidget(self.manual_style_radio)
        source_row.addWidget(self.session_style_radio)
        source_row.addStretch()
        layout.addLayout(source_row)

        style_note = QLabel(
            "Enter an example stamp or load an Inserter session. The example is used as "
            "a pattern to read the current stamp on each main-document page."
        )
        style_note.setWordWrap(True)
        layout.addWidget(style_note)

        self.style_stack = QStackedWidget()
        self.style_stack.setMaximumHeight(56)

        manual = QWidget()
        manual_layout = QHBoxLayout(manual)
        manual_layout.setContentsMargins(0, 0, 0, 0)
        manual_layout.setSpacing(8)
        manual_layout.addWidget(QLabel("Example:"))
        self.example_edit = prepare_line_edit(QLineEdit(), 220)
        self.example_edit.setPlaceholderText("e.g. Page 10A.9.14")
        self.example_edit.setMaximumHeight(FIELD_MIN_HEIGHT)
        self.example_edit.textChanged.connect(self._on_style_changed)
        manual_layout.addWidget(self.example_edit, stretch=1)
        self.style_stack.addWidget(manual)

        session = QWidget()
        session_layout = QHBoxLayout(session)
        session_layout.setContentsMargins(0, 0, 0, 0)
        session_layout.setSpacing(6)
        load_btn = QPushButton("Browse…")
        load_btn.setMaximumHeight(FIELD_MIN_HEIGHT)
        load_btn.clicked.connect(self._browse_session)
        pick_btn = QPushButton("Saved sessions")
        pick_btn.setMaximumHeight(FIELD_MIN_HEIGHT)
        pick_btn.clicked.connect(self._pick_saved_session)
        self.session_summary = QLabel("No session loaded.")
        self.session_summary.setWordWrap(False)
        session_layout.addWidget(load_btn)
        session_layout.addWidget(pick_btn)
        session_layout.addWidget(self.session_summary, stretch=1)
        self.style_stack.addWidget(session)

        layout.addWidget(self.style_stack)
        group.setLayout(layout)
        group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        return group

    def _create_additional_settings_section(self) -> QGroupBox:
        group = QGroupBox("Additional Settings")
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        note = QLabel(
            "Swap Letter is required for Swap Pages. Add Pages uses the Number of digits for the "
            "digit padding and the Range word is used for the TOC (e.g. 30 to 30-03). "
            "The picked colour is used on changed TOC page numbers and, unless you limit it "
            "below, on swapped/added page stamps as well."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        letter_row = QHBoxLayout()
        letter_row.setSpacing(8)
        letter_row.addWidget(QLabel("Swap letter:"))
        self.letter_edit = prepare_line_edit(QLineEdit(), 40)
        self.letter_edit.setMaxLength(1)
        self.letter_edit.setPlaceholderText("e.g. a or B")
        self.letter_edit.setToolTip(
            "Single letter appended to matching swapped pages (upper or lower case)."
        )
        self.letter_edit.textChanged.connect(self._on_additional_settings_changed)
        letter_row.addWidget(self.letter_edit)
        letter_row.addWidget(QLabel("Number of digits:"))
        self.digits_spinbox = prepare_spinbox(QSpinBox(), 56)
        self.digits_spinbox.setMinimum(1)
        self.digits_spinbox.setMaximum(12)
        self.digits_spinbox.setValue(1)
        self.digits_spinbox.setToolTip(
            "Zero-pad extra page suffixes: with 2 digits → 45a, 45-01, 45-02…"
        )
        self.digits_spinbox.valueChanged.connect(self._on_additional_settings_changed)
        letter_row.addWidget(self.digits_spinbox)
        letter_row.addWidget(QLabel("Range word:"))
        self.range_word_combo = prepare_combo_box(QComboBox(), 100)
        self.range_word_combo.setEditable(True)
        self.range_word_combo.addItems(["to", "bis"])
        self.range_word_combo.setCurrentText("to")
        self.range_word_combo.setToolTip(
            "Word between start and end labels in the TOC "
            "(e.g. “45a to 45-03”). Type a custom word if needed."
        )
        self.range_word_combo.currentTextChanged.connect(
            self._on_additional_settings_changed
        )
        if self.range_word_combo.lineEdit() is not None:
            self.range_word_combo.lineEdit().textChanged.connect(
                self._on_additional_settings_changed
            )
        letter_row.addWidget(self.range_word_combo)
        letter_row.addStretch()
        layout.addLayout(letter_row)

        color_row = QHBoxLayout()
        color_row.setSpacing(8)
        color_row.addWidget(QLabel("Font colour RGB:"))
        self.color_r_spin = prepare_spinbox(QSpinBox(), 52)
        self.color_g_spin = prepare_spinbox(QSpinBox(), 52)
        self.color_b_spin = prepare_spinbox(QSpinBox(), 52)
        for spin, value in zip(
            (self.color_r_spin, self.color_g_spin, self.color_b_spin),
            DEFAULT_TOC_FONT_RGB,
        ):
            spin.setRange(0, 255)
            spin.setValue(value)
            spin.valueChanged.connect(self._on_highlight_color_changed)
        color_row.addWidget(self.color_r_spin)
        color_row.addWidget(self.color_g_spin)
        color_row.addWidget(self.color_b_spin)
        self.color_swatch = QLabel("   ")
        self.color_swatch.setFixedSize(32, FIELD_MIN_HEIGHT - 4)
        self._update_color_swatch()
        color_row.addWidget(self.color_swatch)
        pick_color_btn = QPushButton("Pick…")
        pick_color_btn.setMinimumHeight(FIELD_MIN_HEIGHT)
        pick_color_btn.setToolTip(
            "Font colour for updated TOC page numbers and, when enabled below, "
            "for page-number stamps on swapped/added pages."
        )
        pick_color_btn.clicked.connect(self._pick_highlight_color)
        color_row.addWidget(pick_color_btn)
        color_row.addStretch()
        layout.addLayout(color_row)

        self.colour_toc_only_cb = QCheckBox("Apply colour only in the TOC")
        self.colour_toc_only_cb.setToolTip(
            "When checked, the picked colour is used only for updated TOC page numbers. "
            "Swapped and added page stamps keep their normal style."
        )
        layout.addWidget(self.colour_toc_only_cb)

        group.setLayout(layout)
        group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        return group

    def _create_ops_section(self) -> QGroupBox:
        group = QGroupBox("Swap Pages")
        group.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout = QVBoxLayout()
        row = QHBoxLayout()
        add_op = QPushButton("Add PDF")
        add_op.clicked.connect(lambda: self._add_operation())
        remove_op = QPushButton("Remove Selected")
        remove_op.clicked.connect(self._remove_operations)
        row.addWidget(add_op)
        row.addWidget(remove_op)
        row.addStretch()
        layout.addLayout(row)

        self.ops_table = QTableWidget(0, 6)
        self.ops_table.setHorizontalHeaderLabels(
            [
                "Main pages",
                "Current stamp(s)",
                "Replacement PDF",
                "PDF pages",
                "Replacement PDF's page range",
                "Labels preview",
            ]
        )
        ops_header = self.ops_table.horizontalHeader()
        ops_header.setMinimumSectionSize(60)
        ops_header.setStretchLastSection(False)
        for col in range(6):
            ops_header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
        self.ops_table.setWordWrap(True)
        self.ops_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.ops_table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.ops_table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.ops_table.setMinimumHeight(160)
        self.ops_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.ops_table.installEventFilter(self)
        layout.addWidget(self.ops_table, 1)
        self._apply_fixed_column_widths(self.ops_table, OPS_COLUMN_RATIOS)

        note = QLabel(
            "Main pages: <code>18</code> or <code>18-20</code> (contiguous). "
            "Replacement pages: empty = whole file, or <code>1-3</code> / <code>1,2,5</code>. "
            "Edit labels preview to override the stamped page numbers; the TOC uses "
            "the ending part only (e.g. <code>45C</code>)."
        )
        note.setWordWrap(True)
        note.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(note)
        group.setLayout(layout)
        return group

    def _create_add_pages_section(self) -> QGroupBox:
        group = QGroupBox("Add Pages")
        group.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout = QVBoxLayout()

        info = QLabel(
            "Insert PDF pages after a chosen main-document page. "
            "Page numbering follows that page (e.g. after 30 → 30-1, 30-2…). "
            "Only the TOC page number for that page is updated "
            "(e.g. <code>30 to 30-3</code>) in the picked font colour."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info)

        row = QHBoxLayout()
        add_btn = QPushButton("Add PDF")
        add_btn.clicked.connect(self._add_insertion_files)
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self._remove_insertions)
        row.addWidget(add_btn)
        row.addWidget(remove_btn)
        row.addStretch()
        layout.addLayout(row)

        self.insert_table = QTableWidget()
        self.insert_table.setColumnCount(4)
        self.insert_table.setHorizontalHeaderLabels(
            ["Split?", "File Name", "Pages", "Insert Location"]
        )
        header = self.insert_table.horizontalHeader()
        header.setMinimumSectionSize(60)
        header.setStretchLastSection(False)
        for col in range(4):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
        self.insert_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.insert_table.setSelectionMode(
            QTableWidget.SelectionMode.ExtendedSelection
        )
        self.insert_table.setWordWrap(True)
        self.insert_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.insert_table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.insert_table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.insert_table.setMinimumHeight(160)
        self.insert_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.insert_table.installEventFilter(self)
        layout.addWidget(self.insert_table, 1)
        self._apply_fixed_column_widths(self.insert_table, INSERT_COLUMN_RATIOS)

        self.insert_total_label = QLabel("Pages to add: 0")
        self.insert_total_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        layout.addWidget(self.insert_total_label)

        group.setLayout(layout)
        return group

    def eventFilter(self, obj, event):  # noqa: N802 - Qt naming
        if event.type() == QEvent.Type.Resize and isinstance(obj, QTableWidget):
            if obj is self.ops_table:
                self._apply_fixed_column_widths(self.ops_table, OPS_COLUMN_RATIOS)
                self.ops_table.resizeRowsToContents()
            elif obj is self.insert_table:
                self._apply_fixed_column_widths(
                    self.insert_table, INSERT_COLUMN_RATIOS
                )
                self.insert_table.resizeRowsToContents()
        return super().eventFilter(obj, event)

    def _apply_fixed_column_widths(
        self, table: QTableWidget, ratios: Tuple[int, ...]
    ) -> None:
        """Assign fixed proportional widths that fill the table viewport."""
        width = table.viewport().width()
        if width <= 0:
            width = max(table.width() - 2, 1)
        total = sum(ratios) or 1
        used = 0
        last = len(ratios) - 1
        for col, ratio in enumerate(ratios):
            if col == last:
                col_width = max(width - used, 1)
            else:
                col_width = max(int(width * ratio / total), 1)
                used += col_width
            table.setColumnWidth(col, col_width)

    def _readonly_item(
        self,
        text: str,
        *,
        align: Optional[Qt.AlignmentFlag] = None,
        selectable: bool = True,
    ) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        flags = Qt.ItemFlag.ItemIsEnabled
        if selectable:
            flags |= Qt.ItemFlag.ItemIsSelectable
        item.setFlags(flags)
        item.setForeground(READONLY_TEXT)
        if align is not None:
            item.setTextAlignment(align)
        return item

    def _apply_styling(self) -> None:
        lime = LIME_GREEN.name()
        style = f"""
            QPushButton {{
                background-color: {lime};
                color: #000000;
                font-weight: bold;
                font-size: 14px;
                border: 2px solid {lime};
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
            """
        self.apply_primary_button_style(style)

    def apply_primary_button_style(self, stylesheet: str) -> None:
        self.process_btn.setStyleSheet(stylesheet)
        self.save_session_btn.setStyleSheet(stylesheet)

    def _save_session(self) -> None:
        window = self.window()
        if hasattr(window, "_save_session"):
            window._save_session()

    def export_session_data(self) -> Dict[str, Any]:
        """Serialize Swap-tab state for the shared session JSON."""
        return {
            "main_file_path": self.main_file_path,
            "style": {
                "source": (
                    "session" if self.session_style_radio.isChecked() else "manual"
                ),
                "example_text": self.example_edit.text().strip(),
                "session_path": self.session_path,
                "session_example_text": self.session_example_text,
            },
            "additional_settings": {
                "swap_letter": self._current_swap_letter(),
                "range_word": self._current_range_word(),
                "num_digits": self._current_num_digits(),
                "toc_highlight_rgb": list(self._current_highlight_rgb()),
                "colour_toc_only": self.colour_toc_only_cb.isChecked(),
            },
            "operations": [op.to_dict() for op in self.operations],
            "insertions": [seg.to_dict() for seg in self.insertions],
        }

    def apply_session_data(self, data: Dict[str, Any]) -> None:
        """Restore Swap-tab state from a session ``swapper`` block."""
        if not data:
            return

        missing: List[str] = []
        self.operations.clear()
        self.insertions.clear()
        self.toc_info = None
        self.page_number_texts.clear()
        self.source_page_counts.clear()
        self.main_file_path = None
        self.main_page_count = 0
        self.stamp_settings = None
        self.session_path = None
        self.session_example_text = ""
        self.main_label.setText("No main PDF selected.")

        settings = data.get("additional_settings", {})
        if isinstance(settings, dict):
            letter = str(settings.get("swap_letter", "") or "")
            self.letter_edit.setText(letter[:1] if letter else "")
            range_word = str(settings.get("range_word", "to") or "to")
            self.range_word_combo.setCurrentText(range_word)
            try:
                self.digits_spinbox.setValue(max(1, int(settings.get("num_digits", 1))))
            except (TypeError, ValueError):
                self.digits_spinbox.setValue(1)
            rgb = settings.get("toc_highlight_rgb", list(DEFAULT_TOC_FONT_RGB))
            if isinstance(rgb, (list, tuple)) and len(rgb) >= 3:
                self.color_r_spin.setValue(int(rgb[0]))
                self.color_g_spin.setValue(int(rgb[1]))
                self.color_b_spin.setValue(int(rgb[2]))
                self._update_color_swatch()
            self.colour_toc_only_cb.setChecked(
                bool(settings.get("colour_toc_only", False))
            )

        style = data.get("style", {})
        if not isinstance(style, dict):
            style = {}
        example = str(style.get("example_text", "") or "")
        self.example_edit.blockSignals(True)
        self.example_edit.setText(example)
        self.example_edit.blockSignals(False)
        self.session_example_text = str(style.get("session_example_text", "") or "")
        session_path = style.get("session_path")
        source = str(style.get("source", "manual") or "manual").lower()

        if source == "session" and session_path and Path(str(session_path)).is_file():
            ok = self._load_session_style(Path(str(session_path)), show_status=False)
            if not ok:
                self.manual_style_radio.setChecked(True)
        elif source == "session" and self.session_example_text:
            # Style text was saved without a readable session file — use manual example.
            if not example:
                self.example_edit.setText(self.session_example_text)
            self.manual_style_radio.setChecked(True)
        else:
            self.manual_style_radio.setChecked(True)

        main_path = data.get("main_file_path")
        if main_path and Path(str(main_path)).is_file():
            path = str(main_path)
            try:

                def _load():
                    from PyPDF2 import PdfReader

                    count = len(PdfReader(path, strict=False).pages)
                    toc = None
                    try:
                        toc = extract_toc(path, lambda p: p)
                    except Exception:
                        toc = None
                    return count, toc

                count, toc_info = self._run_with_progress(
                    "Loading Session",
                    "Reading swap main PDF…",
                    _load,
                )
            except Exception:
                missing.append(path)
            else:
                self.main_file_path = path
                self.main_page_count = count
                self.toc_info = toc_info
                self.main_label.setText(f"Main: {Path(path).name} ({count} page(s))")
        elif main_path:
            missing.append(str(main_path))

        raw_ops = data.get("operations", [])
        if isinstance(raw_ops, list):
            for raw in raw_ops:
                if not isinstance(raw, dict):
                    continue
                op = SwapOperation.from_dict(raw)
                if op.source_path and not Path(op.source_path).is_file():
                    missing.append(op.source_path)
                    continue
                if op.source_path:
                    self._ensure_source_count(op.source_path)
                self.operations.append(op)

        raw_inserts = data.get("insertions", [])
        if isinstance(raw_inserts, list):
            for raw in raw_inserts:
                if not isinstance(raw, dict):
                    continue
                seg = InsertionSegment.from_dict(raw)
                if seg.file_path and not Path(seg.file_path).is_file():
                    missing.append(seg.file_path)
                    continue
                if seg.file_path:
                    self._ensure_source_count(seg.file_path)
                self.insertions.append(seg)

        self._on_style_changed()
        self._refresh_ops_table()
        self._refresh_insert_table()
        self._update_process_enabled()
        if self.main_file_path:
            self._set_status(
                f"Loaded swap session — {Path(self.main_file_path).name}."
            )
        if missing:
            QMessageBox.warning(
                self,
                "Missing Swap Files",
                "The following swap file paths from the session were not found "
                "and were skipped:\n\n" + "\n".join(dict.fromkeys(missing)),
            )

    def _set_status(self, message: str) -> None:
        self.status_label.setText(message)

    def _run_with_progress(self, title: str, label: str, work):
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

    def _on_style_source_changed(self, *_args) -> None:
        if self.session_style_radio.isChecked():
            self.style_stack.setCurrentIndex(1)
        else:
            self.style_stack.setCurrentIndex(0)
            self.stamp_settings = None
        self._on_style_changed()

    def _on_style_changed(self, *_args) -> None:
        self.page_number_position = self._current_position_key()
        self.page_number_texts.clear()
        self._refresh_ops_table()
        self._refresh_insert_table()
        self._update_process_enabled()

    def _on_additional_settings_changed(self, *_args) -> None:
        if not hasattr(self, "ops_table"):
            return
        for op in self.operations:
            op.custom_labels = []
        self._refresh_ops_table()
        self._update_process_enabled()

    def _on_highlight_color_changed(self, *_args) -> None:
        self._update_color_swatch()

    def _update_color_swatch(self) -> None:
        r = self.color_r_spin.value()
        g = self.color_g_spin.value()
        b = self.color_b_spin.value()
        self.color_swatch.setStyleSheet(
            f"background-color: rgb({r}, {g}, {b}); border: 1px solid #888;"
        )

    def _pick_highlight_color(self) -> None:
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

    def _current_swap_letter(self) -> str:
        return self.letter_edit.text().strip()

    def _current_range_word(self) -> str:
        text = self.range_word_combo.currentText().strip()
        return text or "to"

    def _current_num_digits(self) -> int:
        return max(1, int(self.digits_spinbox.value()))

    def _current_highlight_rgb(self) -> tuple:
        return (
            self.color_r_spin.value(),
            self.color_g_spin.value(),
            self.color_b_spin.value(),
        )

    def _current_position_key(self) -> str:
        if self.session_style_radio.isChecked() and self.stamp_settings is not None:
            return position_key_from_name(self.stamp_settings.position_name)
        return DEFAULT_PAGE_NUMBER_POSITION

    def _current_example_text(self) -> str:
        if self.session_style_radio.isChecked():
            return (self.session_example_text or "").strip()
        return self.example_edit.text().strip()

    def _browse_session(self) -> None:
        start = str(session_directory())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Inserter Session",
            start,
            "Session JSON (*.json)",
        )
        if path:
            self._load_session_style(Path(path))

    def _pick_saved_session(self) -> None:
        sessions = list_session_files()
        if not sessions:
            QMessageBox.information(
                self,
                "No sessions",
                f"No saved sessions found in:\n{session_directory()}",
            )
            return
        from PyQt6.QtWidgets import QInputDialog

        labels = [summary for _, summary in sessions]
        choice, ok = QInputDialog.getItem(
            self,
            "Saved sessions",
            "Select an Inserter session:",
            labels,
            0,
            False,
        )
        if not ok or not choice:
            return
        for path, summary in sessions:
            if summary == choice:
                self._load_session_style(path)
                return

    def _load_session_style(self, path: Path, show_status: bool = True) -> bool:
        """
        Load Insert-tab numbering from a session file for stamp style.

        Returns True when numbering was applied.
        """
        try:
            data = load_session(path)
        except (OSError, ValueError) as exc:
            if show_status:
                QMessageBox.critical(
                    self, "Load Failed", f"Could not load session:\n{path}\n\n{exc}"
                )
            return False
        numbering = numbering_from_session(data)
        if not numbering:
            if show_status:
                QMessageBox.warning(
                    self,
                    "No numbering",
                    "This session file has no Insert-tab numbering settings.",
                )
            return False

        sample = max(1, self.main_page_count // 2) if self.main_page_count else 1
        example = example_text_from_numbering(numbering, sample_page=sample)
        settings = page_number_settings_from_session(numbering)
        position_key = position_key_from_name(settings.position_name)

        self.session_path = str(path)
        self.session_example_text = example
        self.stamp_settings = settings
        self.page_number_position = position_key

        if not self.example_edit.text().strip():
            self.example_edit.blockSignals(True)
            self.example_edit.setText(example)
            self.example_edit.blockSignals(False)

        self.session_summary.setText(f"{path.name} — {example}")
        self.session_style_radio.setChecked(True)
        self._on_style_changed()
        if show_status:
            self._set_status(f"Loaded page style from {path.name}.")
        return True

    def _select_main(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Main PDF", "", "PDF Files (*.pdf)"
        )
        if not path:
            return
        self._set_status("Loading main PDF…")

        def _load():
            from PyPDF2 import PdfReader

            count = len(PdfReader(path, strict=False).pages)
            toc = None
            try:
                toc = extract_toc(path, lambda p: p)
            except Exception:
                toc = None
            return count, toc

        try:
            count, toc_info = self._run_with_progress(
                "Loading PDF",
                "Reading page count and table of contents…",
                _load,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Load Failed", str(exc))
            self._set_status("Ready")
            return

        self.main_file_path = path
        self.main_page_count = count
        self.toc_info = toc_info
        self.page_number_texts = {}
        self.operations.clear()
        self.insertions.clear()
        self.main_label.setText(f"Main: {Path(path).name} ({count} page(s))")
        self._refresh_ops_table()
        self._refresh_insert_table()
        self._update_process_enabled()
        toc_note = (
            f" — {len(toc_info.entries)} TOC entries"
            if toc_info and toc_info.entries
            else ""
        )
        self._set_status(f"Loaded {Path(path).name} — {count} pages{toc_note}.")

    def _stamp_text_for_page(self, physical_page: int) -> str:
        """Read the body-page stamp matching the configured example style."""
        if physical_page in self.page_number_texts:
            return self.page_number_texts[physical_page]
        if not self.main_file_path or physical_page < 1:
            return ""
        example = self._current_example_text()
        if not example:
            return ""
        text = extract_page_number_base_text(
            self.main_file_path,
            physical_page - 1,
            example=example,
            expected_page=physical_page,
            position=self._current_position_key(),
        )
        cleaned = (text or "").strip()
        if cleaned and cleaned != str(physical_page):
            self.page_number_texts[physical_page] = cleaned
            return cleaned
        self.page_number_texts[physical_page] = ""
        return ""

    def _resolve_stamp_base(self, physical_page: int) -> str:
        found = self._stamp_text_for_page(physical_page)
        return found if found else str(physical_page)

    def _ensure_source_count(self, path: str) -> int:
        if path in self.source_page_counts:
            return self.source_page_counts[path]
        try:
            count = self.pdf_processor.get_page_count(path, convert_word=False)
        except Exception:
            count = 0
        self.source_page_counts[path] = count
        return count

    def _add_operation(self, target_page: int = 1) -> None:
        if not self.main_file_path:
            QMessageBox.warning(
                self, "No main PDF", "Select a main PDF before adding swaps."
            )
            return
        if not self._current_example_text():
            QMessageBox.warning(
                self,
                "Page style needed",
                "Enter an example stamp text or load an Inserter session first.",
            )
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Select Replacement PDF", "", "PDF Files (*.pdf)"
        )
        if not path:
            return
        if path == self.main_file_path:
            QMessageBox.warning(
                self, "Same file", "Choose a different PDF as the replacement."
            )
            return
        try:
            count = self._ensure_source_count(path)
        except Exception as exc:
            QMessageBox.warning(
                self, "Skip file", f"Could not read {Path(path).name}:\n{exc}"
            )
            return

        page = max(1, min(target_page, self.main_page_count or 1))
        # If replacement has multiple pages, default main range to the same length.
        if count > 1 and page + count - 1 <= (self.main_page_count or 1):
            spec = f"{page}-{page + count - 1}"
        elif count > 1:
            spec = f"{page}-{self.main_page_count}"
        else:
            spec = str(page)
        self.operations.append(
            SwapOperation(
                target_pages_spec=spec,
                source_path=path,
                source_pages_spec="",
            )
        )
        self._refresh_ops_table()
        self._update_process_enabled()

    def _remove_operations(self) -> None:
        rows = sorted(
            {idx.row() for idx in self.ops_table.selectedIndexes()},
            reverse=True,
        )
        for row in rows:
            if 0 <= row < len(self.operations):
                del self.operations[row]
        self._refresh_ops_table()
        self._update_process_enabled()

    def _browse_replacement(self, op_id: str) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Replacement PDF", "", "PDF Files (*.pdf)"
        )
        if not path or path == self.main_file_path:
            return
        self._ensure_source_count(path)
        op = self._find_op(op_id)
        if op:
            op.source_path = path
            self._refresh_ops_table()

    def _target_pages_safe(self, op: SwapOperation) -> List[int]:
        if not self.main_page_count:
            return []
        try:
            return op.target_pages(self.main_page_count)
        except ValueError:
            return []

    def _stamps_for_targets(self, pages: List[int]) -> str:
        if not pages:
            return "(invalid pages)"
        parts = []
        for p in pages:
            stamp = self._stamp_text_for_page(p) or "(not found)"
            parts.append(stamp)
        return ", ".join(parts)

    def _refresh_ops_table(self) -> None:
        self.ops_table.setRowCount(0)
        for op in self.operations:
            row = self.ops_table.rowCount()
            self.ops_table.insertRow(row)

            pages_edit = QLineEdit(op.target_pages_spec)
            pages_edit.setPlaceholderText("e.g. 18-20")
            pages_edit.setMinimumHeight(FIELD_MIN_HEIGHT)
            pages_edit.editingFinished.connect(
                lambda oid=op.id, edit=pages_edit: self._on_target_spec_changed(
                    oid, edit.text()
                )
            )
            self.ops_table.setCellWidget(row, 0, pages_edit)

            targets = self._target_pages_safe(op)
            self.ops_table.setItem(
                row, 1, self._readonly_item(self._stamps_for_targets(targets))
            )

            src_row = QWidget()
            src_layout = QHBoxLayout(src_row)
            src_layout.setContentsMargins(2, 0, 2, 0)
            src_layout.setSpacing(4)
            name = Path(op.source_path).name if op.source_path else "(none)"
            name_label = QLabel(name)
            name_label.setToolTip(op.source_path or "")
            name_label.setWordWrap(True)
            name_label.setStyleSheet(f"color: {READONLY_TEXT.name()};")
            browse = QPushButton("…")
            browse.setFixedWidth(28)
            browse.setMinimumHeight(FIELD_MIN_HEIGHT)
            browse.clicked.connect(
                lambda _=False, oid=op.id: self._browse_replacement(oid)
            )
            src_layout.addWidget(name_label, stretch=1)
            src_layout.addWidget(browse)
            self.ops_table.setCellWidget(row, 2, src_row)

            pdf_pages = self._ensure_source_count(op.source_path) if op.source_path else 0
            self.ops_table.setItem(
                row,
                3,
                self._readonly_item(
                    str(pdf_pages) if pdf_pages else "?",
                    align=Qt.AlignmentFlag.AlignCenter,
                ),
            )

            src_pages_edit = QLineEdit(op.source_pages_spec)
            src_pages_edit.setPlaceholderText("all pages")
            src_pages_edit.setMinimumHeight(FIELD_MIN_HEIGHT)
            src_pages_edit.editingFinished.connect(
                lambda oid=op.id, edit=src_pages_edit: self._on_pages_spec_changed(
                    oid, edit.text()
                )
            )
            self.ops_table.setCellWidget(row, 4, src_pages_edit)

            labels_edit = prepare_line_edit(QLineEdit(), 120)
            labels_edit.setPlaceholderText("e.g. Page 10A.9.45C, Page 10A.9.46C")
            labels_edit.setToolTip(
                "Full stamped page numbers for each replacement page, comma-separated. "
                "The TOC is updated using the ending part only (e.g. 45C)."
            )
            labels_edit.setText(self._labels_preview_text(op))
            labels_edit.setMinimumHeight(FIELD_MIN_HEIGHT)
            labels_edit.editingFinished.connect(
                lambda oid=op.id, edit=labels_edit: self._on_labels_preview_changed(
                    oid, edit.text()
                )
            )
            self.ops_table.setCellWidget(row, 5, labels_edit)

        self._apply_fixed_column_widths(self.ops_table, OPS_COLUMN_RATIOS)
        self.ops_table.resizeRowsToContents()

    def _find_op(self, op_id: str) -> Optional[SwapOperation]:
        for op in self.operations:
            if op.id == op_id:
                return op
        return None

    def _on_target_spec_changed(self, op_id: str, text: str) -> None:
        op = self._find_op(op_id)
        if op:
            op.target_pages_spec = text.strip() or "1"
            op.custom_labels = []
            self._update_preview_cells(op)

    def _on_pages_spec_changed(self, op_id: str, text: str) -> None:
        op = self._find_op(op_id)
        if op:
            op.source_pages_spec = text.strip()
            op.custom_labels = []
            self._update_preview_cells(op)

    def _source_page_count_for_op(self, op: SwapOperation) -> int:
        count = self._ensure_source_count(op.source_path) if op.source_path else 0
        try:
            if count > 0:
                return len(parse_pages_spec(op.source_pages_spec, count))
        except ValueError:
            pass
        return 1

    def _default_labels(self, op: SwapOperation) -> List[str]:
        targets = self._target_pages_safe(op)
        if not targets:
            return []
        letter = self._current_swap_letter()
        try:
            normalize_swap_letter(letter)
        except ValueError:
            return []
        bases = [self._resolve_stamp_base(p) for p in targets]
        return labels_for_multi_swap(
            bases,
            self._source_page_count_for_op(op),
            letter=letter,
            num_digits=self._current_num_digits(),
        )

    def _labels_preview_text(self, op: SwapOperation) -> str:
        labels = op.custom_labels if op.custom_labels else self._default_labels(op)
        return ", ".join(labels)

    def _parse_labels_preview(self, text: str) -> List[str]:
        return [part.strip() for part in (text or "").split(",") if part.strip()]

    def _on_labels_preview_changed(self, op_id: str, text: str) -> None:
        op = self._find_op(op_id)
        if op is None:
            return
        parsed = self._parse_labels_preview(text)
        default = self._default_labels(op)
        if parsed == default:
            op.custom_labels = []
        else:
            op.custom_labels = parsed
        self._update_preview_cells(op, refresh_labels=False)

    def _update_preview_cells(self, op: SwapOperation, *, refresh_labels: bool = True) -> None:
        for row, existing in enumerate(self.operations):
            if existing.id != op.id:
                continue
            targets = self._target_pages_safe(op)
            self.ops_table.setItem(
                row, 1, self._readonly_item(self._stamps_for_targets(targets))
            )

            pdf_pages = (
                self._ensure_source_count(op.source_path) if op.source_path else 0
            )
            self.ops_table.setItem(
                row,
                3,
                self._readonly_item(
                    str(pdf_pages) if pdf_pages else "?",
                    align=Qt.AlignmentFlag.AlignCenter,
                ),
            )

            if refresh_labels:
                widget = self.ops_table.cellWidget(row, 5)
                if isinstance(widget, QLineEdit):
                    widget.blockSignals(True)
                    widget.setText(self._labels_preview_text(op))
                    widget.blockSignals(False)
            self.ops_table.resizeRowToContents(row)
            break

    def _update_process_enabled(self) -> None:
        has_swaps = bool(self.operations) and all(
            op.source_path for op in self.operations
        )
        has_inserts = bool(self.insertions) and all(
            seg.file_path for seg in self.insertions
        )
        letter_ok = True
        if has_swaps:
            try:
                normalize_swap_letter(self._current_swap_letter())
            except ValueError:
                letter_ok = False
        ready = (
            bool(self.main_file_path)
            and bool(self._current_example_text())
            and (has_swaps or has_inserts)
            and letter_ok
        )
        self.process_btn.setEnabled(ready)

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
        if not self.main_file_path:
            QMessageBox.warning(
                self, "No main PDF", "Select a main PDF before adding pages."
            )
            return
        if not self._current_example_text():
            QMessageBox.warning(
                self,
                "Page style needed",
                "Enter an example stamp text or load an Inserter session first.",
            )
            return

        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select PDFs to Add", "", "PDF Files (*.pdf)"
        )
        if not paths:
            return

        added = 0
        for path in paths:
            if path == self.main_file_path:
                continue
            if self._source_already_added(path):
                continue
            try:
                self._ensure_source_count(path)
            except Exception as exc:
                QMessageBox.warning(
                    self, "Skip file", f"Could not read {Path(path).name}:\n{exc}"
                )
                continue
            self.insertions.append(InsertionSegment(file_path=path))
            added += 1

        self._refresh_insert_table()
        self._update_process_enabled()
        if added:
            self._set_status(f"Added {added} PDF(s) under Add Pages.")

    def _remove_insertions(self) -> None:
        selected_rows = sorted(
            {idx.row() for idx in self.insert_table.selectedIndexes()},
            reverse=True,
        )
        if not selected_rows:
            return

        # Map table rows -> segment ids (pages column stores segment id).
        ids_to_remove = set()
        for row in selected_rows:
            item = self.insert_table.item(row, 2)
            if item is not None:
                seg_id = item.data(Qt.ItemDataRole.UserRole)
                if seg_id:
                    ids_to_remove.add(str(seg_id))

        if not ids_to_remove:
            return
        self.insertions = [
            seg for seg in self.insertions if seg.id not in ids_to_remove
        ]
        self._refresh_insert_table()
        self._update_process_enabled()

    def _open_split_dialog(self, file_path: str) -> None:
        page_count = self._ensure_source_count(file_path)
        if page_count <= 0:
            QMessageBox.warning(
                self, "Cannot Split", "This file has no pages to split."
            )
            return
        existing = self._segments_for_file(file_path)
        initial_groups = [
            seg.pages_spec or format_pages_display("", page_count) for seg in existing
        ]
        dialog = SplitPagesDialog(
            Path(file_path).name,
            page_count,
            initial_groups=initial_groups,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        groups = dialog.groups()
        self._apply_split_groups(file_path, groups, existing)
        self._refresh_insert_table()
        self._update_process_enabled()

    def _apply_split_groups(
        self,
        file_path: str,
        groups: List[str],
        previous_segments: List[InsertionSegment],
    ) -> None:
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

    def _refresh_insert_table(self) -> None:
        if not hasattr(self, "insert_table"):
            return
        self.insert_table.clearSpans()
        self.insert_table.setRowCount(0)
        self._insert_controls.clear()

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

            page_count = self._ensure_source_count(file_path) if file_path else 0
            start_row = self.insert_table.rowCount()

            for offset, segment in enumerate(group):
                row = self.insert_table.rowCount()
                self.insert_table.insertRow(row)

                if offset == 0:
                    split_btn = QPushButton("split?")
                    split_btn.setToolTip(
                        "Split this PDF into page groups for different insert locations"
                    )
                    split_btn.clicked.connect(
                        lambda _checked=False, fp=file_path: self._open_split_dialog(fp)
                    )
                    self.insert_table.setCellWidget(row, 0, split_btn)

                    name_item = self._readonly_item(Path(file_path).name)
                    name_item.setData(Qt.ItemDataRole.UserRole, file_path)
                    self.insert_table.setItem(row, 1, name_item)
                else:
                    self.insert_table.setItem(row, 0, self._readonly_item(""))
                    self.insert_table.setItem(row, 1, self._readonly_item(""))

                pages_item = self._readonly_item(
                    format_pages_display(segment.pages_spec, page_count),
                    align=Qt.AlignmentFlag.AlignCenter,
                )
                pages_item.setData(Qt.ItemDataRole.UserRole, segment.id)
                self.insert_table.setItem(row, 2, pages_item)

                insert_widget = QWidget()
                insert_layout = QVBoxLayout()
                insert_layout.setContentsMargins(2, 2, 2, 2)
                insert_layout.setSpacing(2)

                insert_combo = prepare_combo_box(QComboBox(), 80)
                insert_combo.setSizePolicy(
                    QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
                )
                insert_combo.setSizeAdjustPolicy(
                    QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
                )
                insert_combo.setMinimumContentsLength(12)
                insert_combo.currentIndexChanged.connect(
                    lambda _i, sid=segment.id: self._on_insert_combo_changed(sid)
                )

                insert_spinbox = prepare_spinbox(QSpinBox(), 80)
                insert_spinbox.setMinimum(0)
                insert_spinbox.setMaximum(max(self.main_page_count, 0))
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
                self.insert_table.setCellWidget(row, 3, insert_widget)
                self._sync_insert_controls(segment, insert_combo, insert_spinbox)

            rowspan = len(group)
            if rowspan > 1:
                self.insert_table.setSpan(start_row, 0, rowspan, 1)
                self.insert_table.setSpan(start_row, 1, rowspan, 1)

        self._apply_fixed_column_widths(self.insert_table, INSERT_COLUMN_RATIOS)
        self.insert_table.resizeRowsToContents()
        self._update_insert_total()

    def _update_insert_total(self) -> None:
        total = 0
        for seg in self.insertions:
            count = self.source_page_counts.get(seg.file_path, 0)
            total += count_pages_in_spec(seg.pages_spec, count) if count else 0
        if hasattr(self, "insert_total_label"):
            self.insert_total_label.setText(f"Pages to add: {total}")

    def _populate_insert_combo(self, combo: QComboBox) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Beginning (before page 1)", 0)

        if self.toc_info and self.toc_info.entries:
            # Use each TOC entry's printed page (not "end of chapter"), so
            # Add Pages inserts after that line's page and updates that TOC row.
            for entry in self.toc_info.entries:
                insert_page = max(0, int(entry.page_number or 0))
                if insert_page < 1:
                    continue
                label = entry.insert_location_label
                if len(label) > 80:
                    label = label[:77] + "..."
                combo.addItem(
                    f"After: {label} (after page {insert_page})",
                    insert_page,
                )

        combo.addItem("Custom page number...", -1)
        combo.setMaxVisibleItems(max(12, min(40, combo.count())))
        view = combo.view()
        if view is not None:
            view.setTextElideMode(Qt.TextElideMode.ElideRight)
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
            if (
                combo.itemData(index) == segment.insert_after
                and index != combo.count() - 1
            ):
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
        self._update_insert_total()
        self._update_process_enabled()

    def _update_insert_after_page(self, segment_id: str, value: int) -> None:
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
        self._update_insert_total()
        self._update_process_enabled()

    def _process(self) -> None:
        if not self.main_file_path:
            return
        if not self.operations and not self.insertions:
            return
        example = self._current_example_text()
        if not example:
            QMessageBox.warning(
                self,
                "Page style needed",
                "Enter an example stamp text or load an Inserter session first.",
            )
            return
        letter = ""
        if self.operations:
            try:
                letter = normalize_swap_letter(self._current_swap_letter())
            except ValueError as exc:
                QMessageBox.warning(self, "Swap letter needed", str(exc))
                return

        options = SwapOutputOptionsDialog(self)
        if options.exec() != QDialog.DialogCode.Accepted:
            return
        want_entire = options.want_entire()
        want_toc_new = options.want_toc_new()

        entire_path: Optional[str] = None
        toc_new_path: Optional[str] = None
        if want_entire:
            entire_path, _ = QFileDialog.getSaveFileName(
                self, "Save Entire Document PDF", "", "PDF Files (*.pdf)"
            )
            if not entire_path:
                return
        if want_toc_new:
            suggested = ""
            if entire_path:
                base = Path(entire_path)
                suggested = str(
                    base.with_name(f"{base.stem}_TOC_and_new_pages{base.suffix}")
                )
            toc_new_path, _ = QFileDialog.getSaveFileName(
                self,
                "Save TOC + New Pages PDF",
                suggested,
                "PDF Files (*.pdf)",
            )
            if not toc_new_path:
                return
            if entire_path and Path(toc_new_path).resolve() == Path(entire_path).resolve():
                QMessageBox.warning(
                    self,
                    "Same file",
                    "Choose a different path for the TOC + New pages PDF.",
                )
                return

        range_word = self._current_range_word()
        num_digits = self._current_num_digits()
        highlight_rgb = self._current_highlight_rgb()
        colour_toc_only = self.colour_toc_only_cb.isChecked()
        example = self._current_example_text()

        ops = list(self.operations)
        inserts = list(self.insertions)
        position = self._current_position_key()
        stamp_settings = (
            self.stamp_settings if self.session_style_radio.isChecked() else None
        )
        main_path = self.main_file_path
        self._set_status("Processing pages and updating TOC…")
        self.process_btn.setEnabled(False)
        try:

            def _work():
                texts = {}
                for op in ops:
                    try:
                        for page in op.target_pages(self.main_page_count or 1):
                            texts[page] = self._resolve_stamp_base(page)
                    except ValueError:
                        pass
                for seg in inserts:
                    after = seg.insert_after
                    if after >= 1:
                        texts[after] = self._resolve_stamp_base(after)
                toc_info: Optional[TocInfo] = self.toc_info
                if toc_info is None:
                    try:
                        toc_info = extract_toc(main_path, lambda p: p)
                    except Exception:
                        toc_info = None
                return self.page_swapper.process(
                    main_path,
                    ops,
                    output_path=entire_path,
                    toc_info=toc_info,
                    stamp_settings=stamp_settings,
                    page_number_position=position,
                    page_number_texts=texts,
                    example_text=example,
                    swap_letter=letter or "A",
                    range_word=range_word,
                    num_digits=num_digits,
                    toc_highlight_rgb=highlight_rgb,
                    colour_on_pages=not colour_toc_only,
                    insertions=inserts,
                    toc_new_output_path=toc_new_path,
                )

            summary = self._run_with_progress(
                "Processing",
                "Updating pages, TOC, bookmarks, and stamps…",
                _work,
            )
            saved_bits: List[str] = []
            if entire_path:
                saved_bits.append(
                    f"entire document ({summary['output_pages']} pages) → {entire_path}"
                )
            if toc_new_path:
                saved_bits.append(
                    f"TOC + new pages ({summary.get('toc_new_pages', 0)} pages) "
                    f"→ {toc_new_path}"
                )
            self._set_status("Done — " + "; ".join(saved_bits))

            msg = QMessageBox(self)
            msg.setWindowTitle("Success")
            msg.setIcon(QMessageBox.Icon.Information)
            lines: List[str] = ["Saved:", ""]
            if entire_path:
                lines.append(
                    f"• Entire document: {summary['output_pages']} pages\n"
                    f"  {entire_path}"
                )
            if toc_new_path:
                if entire_path:
                    lines.append("")
                lines.append(
                    f"• TOC + New pages: {summary.get('toc_new_pages', 0)} pages\n"
                    f"  {toc_new_path}"
                )
            lines.append("")
            swaps = summary.get("swaps", [])
            if swaps:
                lines.append("Replacement labels:")
                for spec, labels in swaps:
                    lines.append(f"  Main {spec} → {', '.join(labels)}")
            inserts_summary = summary.get("inserts", [])
            if inserts_summary:
                if swaps:
                    lines.append("")
                lines.append("Added pages:")
                for after, labels in inserts_summary:
                    where = (
                        f"after page {after}" if after >= 1 else "at beginning"
                    )
                    lines.append(f"  {where} → {', '.join(labels)}")
            msg.setText("\n".join(lines))

            open_entire_btn = None
            open_toc_btn = None
            open_both_btn = None
            if entire_path:
                open_entire_btn = msg.addButton(
                    "Open Entire PDF", QMessageBox.ButtonRole.AcceptRole
                )
            if toc_new_path:
                open_toc_btn = msg.addButton(
                    "Open TOC + New PDF", QMessageBox.ButtonRole.AcceptRole
                )
            if entire_path and toc_new_path:
                open_both_btn = msg.addButton(
                    "Open Both", QMessageBox.ButtonRole.AcceptRole
                )
            msg.addButton(QMessageBox.StandardButton.Ok)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked == open_both_btn and entire_path and toc_new_path:
                QDesktopServices.openUrl(QUrl.fromLocalFile(entire_path))
                QDesktopServices.openUrl(QUrl.fromLocalFile(toc_new_path))
            elif clicked == open_entire_btn and entire_path:
                QDesktopServices.openUrl(QUrl.fromLocalFile(entire_path))
            elif clicked == open_toc_btn and toc_new_path:
                QDesktopServices.openUrl(QUrl.fromLocalFile(toc_new_path))
        except Exception as exc:
            self._set_status(f"Error: {exc}")
            QMessageBox.critical(self, "Process Failed", str(exc))
        finally:
            self._update_process_enabled()
