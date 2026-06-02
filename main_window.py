"""
Main window module for the Page Numbering Tool application.
Contains the PyQt6 UI components and main application logic.
"""

from typing import List, Optional, Dict, Union
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QFileDialog, QLabel, QSpinBox, QMessageBox, QGroupBox,
    QTableWidget, QTableWidgetItem, QHeaderView
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from pathlib import Path
import sys

from file_handler import FileHandler
from pdf_processor import PDFProcessor


# Pantone 382C - Lime Green (RGB: 206, 220, 0)
LIME_GREEN = QColor(206, 220, 0)


class MainWindow(QMainWindow):
    """
    Main application window for the Page Numbering Tool.
    
    Attributes:
        file_handler: FileHandler instance for file operations
        pdf_processor: PDFProcessor instance for PDF operations
        file_table: QTableWidget for displaying and ordering files
        file_paths: List of file paths in order
        buffer_spinboxes: Dictionary mapping row index to buffer page spinboxes
    """
    
    def __init__(self) -> None:
        """
        Initialize the main window and set up UI components.
        """
        super().__init__()
        self.file_handler: FileHandler = FileHandler()
        self.pdf_processor: PDFProcessor = PDFProcessor()
        self.file_paths: List[str] = []
        self.buffer_pages: Dict[str, int] = {}  # Maps file_path to buffer page count
        self.page_counts: Dict[str, int] = {}  # Maps file_path to page count
        
        self.setWindowTitle("Page Numbering Tool")
        self.setGeometry(100, 100, 900, 700)
        
        self._setup_ui()
        self._apply_lime_green_styling()
    
    def _setup_ui(self) -> None:
        """
        Set up the user interface components.
        """
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout()
        central_widget.setLayout(main_layout)
        
        # File selection section
        file_section = self._create_file_section()
        main_layout.addWidget(file_section)
        
        # File table section
        table_section = self._create_table_section()
        main_layout.addWidget(table_section)
        
        # Action buttons
        button_layout = self._create_button_section()
        main_layout.addLayout(button_layout)
        
        # Status label
        self.status_label = QLabel("Ready")
        main_layout.addWidget(self.status_label)
    
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
        
        remove_file_btn = QPushButton("Remove Selected")
        remove_file_btn.clicked.connect(self._remove_selected_files)
        layout.addWidget(remove_file_btn)
        
        layout.addStretch()
        
        group.setLayout(layout)
        return group
    
    def _create_table_section(self) -> QGroupBox:
        """
        Create the file table section with ordering and buffer pages.
        
        Returns:
            QGroupBox containing the file table
        """
        group = QGroupBox("Files - Order & Buffer Pages")
        layout = QVBoxLayout()
        
        info_label = QLabel(
            "Use the arrow buttons (▲ ▼) to reorder files. Set buffer pages (blank pages) to add after each file."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        # Create table
        self.file_table = QTableWidget()
        self.file_table.setColumnCount(5)
        self.file_table.setHorizontalHeaderLabels(["Order", "File Name", "Pages", "Buffer Pages", ""])
        
        # Set column widths
        header = self.file_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        
        self.file_table.setColumnWidth(0, 100)  # Order column
        self.file_table.setColumnWidth(2, 80)  # Pages column
        self.file_table.setColumnWidth(3, 150)  # Buffer pages column
        self.file_table.setColumnWidth(4, 10)  # Empty column for spacing
        
        self.file_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.file_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.file_table.setMinimumHeight(300)
        
        layout.addWidget(self.file_table)
        
        # Total pages label
        self.total_pages_label = QLabel("Total Pages: 0")
        self.total_pages_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        layout.addWidget(self.total_pages_label)
        
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
        # Apply lime green to the main action button (transformational outcome)
        lime_green_hex = LIME_GREEN.name()
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
            # Calculate page count
            page_count = self.pdf_processor.get_page_count(file_path)
            self.page_counts[file_path] = page_count
            self._refresh_table()
            self._update_total_pages()
    
    def _refresh_table(self) -> None:
        """
        Rebuild the entire table from the file_paths list.
        """
        # Clear the table
        self.file_table.setRowCount(0)
        
        # Rebuild from the list
        for index, file_path in enumerate(self.file_paths):
            row = self.file_table.rowCount()
            self.file_table.insertRow(row)
            
            # Order column - arrow buttons
            order_widget = QWidget()
            order_layout = QHBoxLayout()
            order_layout.setContentsMargins(5, 2, 5, 2)
            order_layout.setSpacing(5)
            
            up_btn = QPushButton("▲")
            up_btn.setMaximumWidth(35)
            up_btn.setMaximumHeight(25)
            up_btn.setToolTip("Move up")
            up_btn.setEnabled(index > 0)
            up_btn.clicked.connect(lambda checked, idx=index: self._move_file_up(idx))
            order_layout.addWidget(up_btn)
            
            down_btn = QPushButton("▼")
            down_btn.setMaximumWidth(35)
            down_btn.setMaximumHeight(25)
            down_btn.setToolTip("Move down")
            down_btn.setEnabled(index < len(self.file_paths) - 1)
            down_btn.clicked.connect(lambda checked, idx=index: self._move_file_down(idx))
            order_layout.addWidget(down_btn)
            
            order_widget.setLayout(order_layout)
            self.file_table.setCellWidget(row, 0, order_widget)
            
            # File name column
            file_name = self.file_handler.get_file_name(file_path)
            name_item = QTableWidgetItem(file_name)
            name_item.setData(Qt.ItemDataRole.UserRole, file_path)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.file_table.setItem(row, 1, name_item)
            
            # Pages column - calculate if not already stored
            if file_path not in self.page_counts:
                # Convert Word files to get accurate page count
                page_count = self.pdf_processor.get_page_count(file_path, convert_word=True)
                self.page_counts[file_path] = page_count
            else:
                page_count = self.page_counts[file_path]
            
            # Display page count
            pages_item = QTableWidgetItem(str(page_count))
            pages_item.setFlags(Qt.ItemFlag.NoItemFlags)
            pages_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.file_table.setItem(row, 2, pages_item)
            
            # Buffer pages column
            buffer_spinbox = QSpinBox()
            buffer_spinbox.setMinimum(0)
            buffer_spinbox.setMaximum(100)
            buffer_spinbox.setValue(self.buffer_pages.get(file_path, 0))
            buffer_spinbox.setSuffix(" pages")
            buffer_spinbox.valueChanged.connect(lambda value, fp=file_path: self._update_buffer_pages(fp, value))
            self.file_table.setCellWidget(row, 3, buffer_spinbox)
            
            # Empty column for spacing
            empty_item = QTableWidgetItem("")
            empty_item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.file_table.setItem(row, 4, empty_item)
        
        # Update total pages after refreshing table
        self._update_total_pages()
    
    def _move_file_up(self, index: int) -> None:
        """
        Move a file up in the list (swap with previous item).
        
        Args:
            index: Index of the file to move up
        """
        if index > 0:
            # Simple list swap
            self.file_paths[index], self.file_paths[index - 1] = (
                self.file_paths[index - 1],
                self.file_paths[index]
            )
            # Refresh table to reflect new order
            self._refresh_table()
    
    def _move_file_down(self, index: int) -> None:
        """
        Move a file down in the list (swap with next item).
        
        Args:
            index: Index of the file to move down
        """
        if index < len(self.file_paths) - 1:
            # Simple list swap
            self.file_paths[index], self.file_paths[index + 1] = (
                self.file_paths[index + 1],
                self.file_paths[index]
            )
            # Refresh table to reflect new order
            self._refresh_table()
    
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
        
        # Refresh table to reflect changes
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
        Calculate and update the total page count display.
        Includes pages from all files plus buffer pages.
        """
        total_pages = 0
        
        for file_path in self.file_paths:
            # Add file pages
            total_pages += self.page_counts.get(file_path, 0)
            # Add buffer pages
            total_pages += self.buffer_pages.get(file_path, 0)
        
        self.total_pages_label.setText(f"Total Pages: {total_pages}")
    
    def _get_ordered_files(self) -> List[str]:
        """
        Get the list of files in their current order.
        
        Returns:
            List of file paths in order
        """
        return self.file_paths.copy()
    
    def _get_buffer_pages(self, file_paths: List[str]) -> List[int]:
        """
        Get buffer page counts for each file.
        
        Args:
            file_paths: List of file paths
            
        Returns:
            List of buffer page counts
        """
        return [self.buffer_pages.get(file_path, 0) for file_path in file_paths]
    
    def _process_files(self) -> None:
        """
        Process files: merge and add page numbers.
        """
        if len(self.file_paths) == 0:
            QMessageBox.warning(self, "No Files", "Please add files before processing.")
            return
        
        # Get output file path
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
            
            # Get ordered files and buffer pages
            file_paths = self._get_ordered_files()
            buffer_pages = self._get_buffer_pages(file_paths)
            
            # Convert Word files to PDF first (page counts already calculated when files were added)
            from pathlib import Path
            pre_converted_pdfs: Dict[str, str] = {}
            
            self._update_status("Converting Word files to PDF...")
            for file_path in file_paths:
                file_ext = Path(file_path).suffix.lower()
                if file_ext in ['.docx', '.doc']:
                    # Convert Word file to PDF for processing
                    try:
                        temp_pdf = self.pdf_processor.convert_word_to_pdf(file_path)
                        pre_converted_pdfs[file_path] = temp_pdf
                    except Exception as e:
                        # If conversion fails, show error
                        self._update_status(f"Error: Failed to convert {Path(file_path).name}: {str(e)}")
                        raise
            
            self._update_status("Merging PDFs and adding page numbers...")
            
            # Process files (pass pre-converted PDFs to avoid double conversion)
            self.pdf_processor.process_files(
                file_paths,
                buffer_pages,
                output_path,
                start_page_number=1,
                pre_converted_pdfs=pre_converted_pdfs
            )
            
            self._update_status(f"Success! Combined PDF saved to: {output_path}")
            QMessageBox.information(
                self,
                "Success",
                f"Files have been combined and page numbers added.\n\nSaved to:\n{output_path}"
            )
            
        except Exception as e:
            self._update_status(f"Error: {str(e)}")
            QMessageBox.critical(
                self,
                "Error",
                f"An error occurred while processing files:\n{str(e)}"
            )
        finally:
            self.process_btn.setEnabled(True)

