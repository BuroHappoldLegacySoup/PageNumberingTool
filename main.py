"""
Main entry point for the Page Numbering Tool application.
"""

import os
import sys


def _ensure_stdio() -> None:
    """
    PyInstaller --noconsole builds leave sys.stdout/stderr as None.
    Anything that writes progress/logging to stdout (e.g. tqdm) then fails with:
    'NoneType' object has no attribute 'write'.
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")


_ensure_stdio()

from PyQt6.QtWidgets import QApplication

from main_window import MainWindow
from session_dialog import SessionStartDialog


def main() -> None:
    """
    Main function to start the application.
    """
    app = QApplication(sys.argv)

    session_path, is_new = SessionStartDialog.run()
    if not is_new and session_path is None:
        sys.exit(0)

    window = MainWindow(
        initial_session_path=session_path if session_path is not None else None
    )
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
