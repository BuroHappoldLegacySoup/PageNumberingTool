"""
Main entry point for the Page Numbering Tool application.
"""

import sys
from pathlib import Path

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
