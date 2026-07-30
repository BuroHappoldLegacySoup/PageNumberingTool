"""
Startup dialog to open an existing session or start a new one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from session_manager import list_session_files, session_directory


class SessionStartDialog(QDialog):
    """Pick an existing session JSON or start a new session."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("The Reportinator — Session")
        self.setMinimumWidth(520)
        self.setMinimumHeight(360)

        self._selected_path: Optional[Path] = None
        self._new_session = False

        layout = QVBoxLayout(self)

        folder = session_directory()
        layout.addWidget(
            QLabel(
                f"Sessions folder:\n{folder}\n\n"
                "Select a saved session to load, or start a new session."
            )
        )

        layout.addWidget(QLabel("Saved sessions (.json):"))

        self.session_list = QListWidget()
        self.session_list.setAlternatingRowColors(True)
        self._populate_sessions()
        self.session_list.itemDoubleClicked.connect(self._on_load_clicked)
        layout.addWidget(self.session_list)

        buttons_row = QHBoxLayout()
        load_btn = QPushButton("Load Selected")
        load_btn.clicked.connect(self._on_load_clicked)
        new_btn = QPushButton("New Session")
        new_btn.clicked.connect(self._on_new_clicked)
        buttons_row.addWidget(load_btn)
        buttons_row.addWidget(new_btn)
        buttons_row.addStretch()
        layout.addLayout(buttons_row)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def _populate_sessions(self) -> None:
        self.session_list.clear()
        sessions = list_session_files()
        if not sessions:
            item = QListWidgetItem("(No saved sessions yet)")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.session_list.addItem(item)
            return
        for path, summary in sessions:
            item = QListWidgetItem(summary)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.session_list.addItem(item)

    def _on_load_clicked(self) -> None:
        item = self.session_list.currentItem()
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
