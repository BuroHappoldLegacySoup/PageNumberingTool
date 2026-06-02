"""
Main entry point for the Page Numbering Tool application.
"""

import sys
from PyQt6.QtWidgets import QApplication
from main_window import MainWindow


def main() -> None:
    """
    Main function to start the application.
    """
    app = QApplication(sys.argv)
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()






