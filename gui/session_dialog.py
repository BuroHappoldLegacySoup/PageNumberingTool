"""
Startup dialog to open an existing session or start a new one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from backend.session_manager import list_session_files, session_directory

COL_NAME = 0
COL_AUTHOR = 1
COL_MODIFIED = 2


class SessionStartDialog(QDialog):
    """Pick an existing session JSON or start a new session."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Session Manager")
        self.setMinimumWidth(680)
        self.setMinimumHeight(420)

        self._selected_path: Optional[Path] = None
        self._new_session = False
        self._folder = session_directory()

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("Select a saved session to load, or start a new session.")
        )

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Session folder:"))
        self.folder_edit = QLineEdit(str(self._folder))
        self.folder_edit.setPlaceholderText("Folder containing session JSON files")
        self.folder_edit.editingFinished.connect(self._on_folder_edit_finished)
        folder_row.addWidget(self.folder_edit, stretch=1)
        change_btn = QPushButton("Change Folder...")
        change_btn.clicked.connect(self._on_change_folder)
        folder_row.addWidget(change_btn)
        layout.addLayout(folder_row)

        layout.addWidget(QLabel("Saved sessions (.json):"))

        self.session_table = QTableWidget(0, 3)
        self.session_table.setHorizontalHeaderLabels(
            ["File Name", "Author", "Last modified"]
        )
        self.session_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.session_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.session_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.session_table.setAlternatingRowColors(True)
        self.session_table.setShowGrid(False)
        self.session_table.verticalHeader().setVisible(False)
        self.session_table.setSortingEnabled(True)
        header = self.session_table.horizontalHeader()
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_AUTHOR, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(
            COL_MODIFIED, QHeaderView.ResizeMode.ResizeToContents
        )
        header.setHighlightSections(False)
        self.session_table.cellDoubleClicked.connect(self._on_load_clicked)
        layout.addWidget(self.session_table)

        self.empty_label = QLabel("No JSON files in this folder.")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_label)

        self._populate_sessions()

        buttons_row = QHBoxLayout()
        load_btn = QPushButton("Load Selected")
        load_btn.clicked.connect(self._on_load_clicked)
        browse_btn = QPushButton("Browse...")
        browse_btn.setToolTip("Open a session JSON file from any location")
        browse_btn.clicked.connect(self._on_browse_file)
        new_btn = QPushButton("New Session")
        new_btn.clicked.connect(self._on_new_clicked)
        buttons_row.addWidget(load_btn)
        buttons_row.addWidget(browse_btn)
        buttons_row.addWidget(new_btn)
        buttons_row.addStretch()
        layout.addLayout(buttons_row)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def _set_folder(self, folder: Path) -> None:
        self._folder = folder
        current = self.folder_edit.text()
        if current != str(folder):
            self.folder_edit.setText(str(folder))
        self._populate_sessions()

    def _on_change_folder(self) -> None:
        start = self._folder if self._folder.is_dir() else Path.home()
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Choose session folder",
            str(start),
        )
        if chosen:
            self._set_folder(Path(chosen))

    def _on_folder_edit_finished(self) -> None:
        text = self.folder_edit.text().strip()
        if not text:
            self.folder_edit.setText(str(self._folder))
            return
        folder = Path(text)
        if folder != self._folder:
            self._set_folder(folder)

    def _on_browse_file(self) -> None:
        start = self._folder if self._folder.is_dir() else Path.home()
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Open session file",
            str(start),
            "Session Files (*.json);;All Files (*)",
        )
        if not chosen:
            return
        path = Path(chosen)
        self._selected_path = path
        self._new_session = False
        self.accept()

    def _populate_sessions(self) -> None:
        self.session_table.setSortingEnabled(False)
        self.session_table.setRowCount(0)
        sessions = (
            list_session_files(self._folder) if self._folder.is_dir() else []
        )
        self.empty_label.setVisible(not sessions)
        for row, entry in enumerate(sessions):
            self.session_table.insertRow(row)
            name_item = QTableWidgetItem(entry.file_name)
            name_item.setData(Qt.ItemDataRole.UserRole, str(entry.path))
            self.session_table.setItem(row, COL_NAME, name_item)
            self.session_table.setItem(row, COL_AUTHOR, QTableWidgetItem(entry.author))
            self.session_table.setItem(
                row, COL_MODIFIED, QTableWidgetItem(entry.last_modified)
            )
        if sessions:
            self.session_table.selectRow(0)
        self.session_table.setSortingEnabled(True)

    def _on_load_clicked(self, *_args) -> None:
        row = self.session_table.currentRow()
        if row < 0:
            return
        item = self.session_table.item(row, COL_NAME)
        if item is None:
            return
        path_str = item.data(Qt.ItemDataRole.UserRole)
        if not path_str:
            return
        self._selected_path = Path(path_str)
        self._new_session = False
        self.accept()

    def _on_new_clicked(self) -> None:
        self._selected_path = None
        self._new_session = True
        self.accept()

    @staticmethod
    def run(parent=None) -> Tuple[Optional[Path], bool]:
        """
        Show the dialog.

        Returns:
            (session_path, is_new_session). Both None/False if cancelled.
        """
        dlg = SessionStartDialog(parent)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None, False
        return dlg._selected_path, dlg._new_session
