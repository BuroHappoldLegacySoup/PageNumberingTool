"""
File handler module for managing file operations.
Handles file selection, validation, and conversion operations.
"""

from typing import List, Tuple, Optional
from pathlib import Path
import os


class FileHandler:
    """
    Handles file operations including validation and path management.
    
    Attributes:
        supported_extensions: List of supported file extensions
    """
    
    def __init__(self) -> None:
        """
        Initialize the FileHandler with supported file extensions.
        """
        self.supported_extensions: List[str] = ['.pdf', '.docx', '.doc']
    
    def is_valid_file(self, file_path: str) -> bool:
        """
        Check if a file path is valid and has a supported extension.
        
        Args:
            file_path: Path to the file to validate
            
        Returns:
            True if file is valid and supported, False otherwise
        """
        if not file_path:
            return False
        
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return False
        
        extension = path.suffix.lower()
        return extension in self.supported_extensions
    
    def get_file_extension(self, file_path: str) -> str:
        """
        Get the file extension from a file path.
        
        Args:
            file_path: Path to the file
            
        Returns:
            File extension (e.g., '.pdf', '.docx')
        """
        return Path(file_path).suffix.lower()
    
    def get_file_name(self, file_path: str) -> str:
        """
        Get the file name from a file path.
        
        Args:
            file_path: Path to the file
            
        Returns:
            File name without directory
        """
        return Path(file_path).name
    
    def validate_files(self, file_paths: List[str]) -> Tuple[List[str], List[str]]:
        """
        Validate a list of file paths.
        
        Args:
            file_paths: List of file paths to validate
            
        Returns:
            Tuple of (valid_files, invalid_files)
        """
        valid_files: List[str] = []
        invalid_files: List[str] = []
        
        for file_path in file_paths:
            if self.is_valid_file(file_path):
                valid_files.append(file_path)
            else:
                invalid_files.append(file_path)
        
        return valid_files, invalid_files






