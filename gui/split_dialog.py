"""
Dialog for splitting an inserted PDF into page groups.
"""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from backend.page_spec import parse_split_groups


class SplitPagesDialog(QDialog):
    """Ask how to split a PDF into page groups for separate insert locations."""

    def __init__(
        self,
        file_name: str,
        page_count: int,
        initial_groups: Optional[List[str]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._page_count = page_count
        self._result_groups: List[str] = []
        self.setWindowTitle("Split PDF pages")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        intro = QLabel(
            f"<b>{file_name}</b> has <b>{page_count}</b> page(s).<br><br>"
            "Enter one insertion group per line. Use a range (<code>1-4</code>) "
            "or individual pages (<code>5,6,7</code>). "
            "Each group can be inserted at a different place in the main document."
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(intro)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("1-4\n5,6\n7")
        if initial_groups:
            self.text_edit.setPlainText("\n".join(initial_groups))
        elif page_count > 0:
            self.text_edit.setPlainText(
                f"1-{page_count}" if page_count > 1 else "1"
            )
        self.text_edit.setMinimumHeight(140)
        layout.addWidget(self.text_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        whole_btn = QPushButton("Use whole file")
        whole_btn.clicked.connect(self._use_whole_file)
        buttons.addButton(whole_btn, QDialogButtonBox.ButtonRole.ResetRole)
        buttons.accepted.connect(self._accept_groups)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _use_whole_file(self) -> None:
        self._result_groups = [""]
        self.accept()

    def _accept_groups(self) -> None:
        try:
            groups = parse_split_groups(self.text_edit.toPlainText(), self._page_count)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid split", str(exc))
            return
        self._result_groups = groups
        self.accept()

    def groups(self) -> List[str]:
        return list(self._result_groups)
