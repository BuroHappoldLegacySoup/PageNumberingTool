"""
PDF processor module for merging PDFs and adding page numbers.
Handles PDF operations including merging, page numbering, and Word to PDF conversion.
"""

from typing import List, Optional, Union, Dict, Tuple
from pathlib import Path
import tempfile
import os

try:
    from PyPDF2 import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    import docx
except ImportError:
    pass


class PDFProcessor:
    """
    Handles PDF operations including merging and page numbering.
    
    Attributes:
        temp_dir: Temporary directory for intermediate files
    """
    
    def __init__(self) -> None:
        """
        Initialize the PDFProcessor with a temporary directory.
        """
        self.temp_dir: Optional[str] = None
    
    def _create_temp_dir(self) -> str:
        """
        Create a temporary directory for intermediate files.
        
        Returns:
            Path to the temporary directory
        """
        if self.temp_dir is None:
            self.temp_dir = tempfile.mkdtemp()
        return self.temp_dir
    
    def _cleanup_temp_dir(self) -> None:
        """
        Clean up temporary directory and its contents.
        """
        if self.temp_dir and os.path.exists(self.temp_dir):
            import shutil
            shutil.rmtree(self.temp_dir)
            self.temp_dir = None
    
    def _cleanup_word_processes(self) -> None:
        """
        Attempt to clean up any hanging Word processes.
        This helps with COM automation issues.
        """
        try:
            import subprocess
            import platform
            
            if platform.system() == "Windows":
                # Try to kill any WINWORD.EXE processes
                # Note: This is a last resort and might close user's Word windows
                # We'll use taskkill but only if absolutely necessary
                try:
                    # Check if Word is running
                    result = subprocess.run(
                        ["tasklist", "/FI", "IMAGENAME eq WINWORD.EXE"],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if "WINWORD.EXE" in result.stdout:
                        # Word is running - we'll let docx2pdf handle it
                        # Don't force kill as it might be user's open document
                        pass
                except Exception:
                    pass  # Ignore errors in cleanup attempt
        except Exception:
            pass  # Ignore all cleanup errors
    
    def convert_word_to_pdf(self, word_path: str, output_path: Optional[str] = None) -> str:
        """
        Convert a Word document to PDF using docx2pdf, preserving images and tables.
        
        Args:
            word_path: Path to the Word document (.docx or .doc)
            output_path: Optional output path for the PDF
            
        Returns:
            Path to the converted PDF file
        """
        if output_path is None:
            temp_dir = self._create_temp_dir()
            output_path = os.path.join(temp_dir, Path(word_path).stem + '.pdf')
        
        # Convert Word document to PDF using docx2pdf (uses Microsoft Word COM automation)
        # This preserves all formatting, images, and tables
        try:
            from docx2pdf import convert as docx2pdf_convert
        except ImportError:
            raise ImportError(
                "docx2pdf library is required for Word to PDF conversion. "
                "Please install it with: pip install docx2pdf"
            )
        
        # Try conversion with retry logic to handle COM automation issues
        max_retries = 3
        last_error = None
        
        for attempt in range(max_retries):
            try:
                # docx2pdf requires absolute paths
                word_abs_path = os.path.abspath(word_path)
                output_abs_path = os.path.abspath(output_path)
                
                # Convert using docx2pdf - this uses Word's built-in conversion
                # which preserves all images, tables, and formatting
                # Works with both .docx and .doc files (Word handles the conversion)
                docx2pdf_convert(word_abs_path, output_abs_path)
                
                # Verify the PDF was created
                if not os.path.exists(output_abs_path):
                    raise FileNotFoundError(f"PDF conversion failed: {output_abs_path} not created")
                
                # Success - break out of retry loop
                break
                
            except Exception as e:
                last_error = e
                error_msg = str(e).lower()
                
                # Check if PDF was actually created despite the error
                # Sometimes docx2pdf succeeds but throws a Quit error
                if os.path.exists(output_abs_path) and os.path.getsize(output_abs_path) > 0:
                    # PDF was created successfully, ignore the error
                    break
                
                # Check if it's a COM/Quit error that might be recoverable
                if "quit" in error_msg or "com" in error_msg or "application" in error_msg:
                    if attempt < max_retries - 1:
                        # Try to clean up any hanging Word processes
                        try:
                            self._cleanup_word_processes()
                        except Exception:
                            pass  # Ignore cleanup errors
                        
                        # Wait a bit before retrying
                        import time
                        time.sleep(1)
                        continue
                
                # If it's the last attempt or not a retryable error, raise it
                if attempt == max_retries - 1:
                    # Check one more time if PDF was created
                    if os.path.exists(output_abs_path) and os.path.getsize(output_abs_path) > 0:
                        # PDF exists, conversion actually succeeded
                        break
                    
                    # Provide helpful error message
                    if "quit" in error_msg or "com" in error_msg:
                        raise RuntimeError(
                            f"Failed to convert Word document to PDF after {max_retries} attempts.\n"
                            f"Error: {str(e)}\n\n"
                            "This is often caused by Word COM automation issues. Try:\n"
                            "1. Close all Word windows (check Task Manager for WINWORD.EXE processes)\n"
                            "2. Restart your computer if the issue persists\n"
                            "3. Make sure the document is not corrupted or password-protected\n"
                            "4. Try opening the document in Word manually to verify it's not corrupted"
                        ) from e
                    else:
                        raise RuntimeError(
                            f"Failed to convert Word document to PDF: {str(e)}\n"
                            "Make sure Microsoft Word is installed and the document is not open in Word."
                        ) from e
        
        # Final check - if PDF doesn't exist after all attempts, raise error
        if not os.path.exists(output_abs_path) or os.path.getsize(output_abs_path) == 0:
            if last_error:
                raise RuntimeError(
                    f"Failed to convert Word document to PDF after {max_retries} attempts.\n"
                    f"Last error: {str(last_error)}\n\n"
                    "The PDF file was not created. Please check:\n"
                    "1. Microsoft Word is installed and working\n"
                    "2. The document is not corrupted\n"
                    "3. You have write permissions to the output directory"
                )
            else:
                raise RuntimeError("PDF conversion failed - no PDF file was created.")
        
        return output_path
    
    def get_page_count(self, file_path: str, convert_word: bool = True) -> int:
        """
        Get the number of pages in a PDF or Word document.
        For Word documents, converts to PDF temporarily to get accurate page count.
        
        Args:
            file_path: Path to the PDF or Word document
            convert_word: If True, converts Word files to PDF to get accurate count.
                         If False, returns 0 for Word files (default: True)
            
        Returns:
            Number of pages in the document
        """
        from pathlib import Path
        
        file_ext = Path(file_path).suffix.lower()
        
        if file_ext == '.pdf':
            try:
                reader = PdfReader(file_path)
                return len(reader.pages)
            except Exception:
                return 0
        elif file_ext in ['.docx', '.doc']:
            if not convert_word:
                return 0
            try:
                # Convert Word to PDF temporarily to get accurate page count
                # This ensures we count pages correctly including images and tables
                temp_pdf = self.convert_word_to_pdf(file_path)
                reader = PdfReader(temp_pdf)
                page_count = len(reader.pages)
                # Clean up temporary PDF after getting page count
                try:
                    if os.path.exists(temp_pdf):
                        os.remove(temp_pdf)
                except Exception:
                    pass  # Ignore cleanup errors
                return page_count
            except Exception as e:
                # If conversion fails, return 0
                return 0
        else:
            return 0
    
    def add_buffer_pages(self, pdf_path: str, num_pages: int, output_path: Optional[str] = None) -> str:
        """
        Add buffer (blank) pages to a PDF.
        
        Args:
            pdf_path: Path to the PDF file
            num_pages: Number of buffer pages to add
            output_path: Optional output path for the PDF with buffer pages
            
        Returns:
            Path to the PDF with buffer pages added
        """
        if output_path is None:
            temp_dir = self._create_temp_dir()
            output_path = os.path.join(temp_dir, f"buffered_{Path(pdf_path).name}")
        
        writer = PdfWriter()
        
        # Add original PDF pages
        reader = PdfReader(pdf_path)
        for page in reader.pages:
            writer.add_page(page)
        
        # Add buffer pages
        for _ in range(num_pages):
            # Create a blank page
            c = canvas.Canvas(output_path.replace('.pdf', '_temp.pdf'), pagesize=letter)
            c.showPage()
            c.save()
            
            temp_reader = PdfReader(output_path.replace('.pdf', '_temp.pdf'))
            writer.add_page(temp_reader.pages[0])
            os.remove(output_path.replace('.pdf', '_temp.pdf'))
        
        with open(output_path, 'wb') as output_file:
            writer.write(output_file)
        
        return output_path
    
    def merge_pdfs(self, pdf_paths: List[str], output_path: str) -> None:
        """
        Merge multiple PDF files into a single PDF.
        
        Args:
            pdf_paths: List of paths to PDF files to merge
            output_path: Path where the merged PDF should be saved
        """
        writer = PdfWriter()
        
        for pdf_path in pdf_paths:
            reader = PdfReader(pdf_path)
            for page in reader.pages:
                writer.add_page(page)
        
        with open(output_path, 'wb') as output_file:
            writer.write(output_file)
    
    def add_page_numbers(self, pdf_path: str, output_path: str, start_number: int = 1) -> None:
        """
        Add page numbers to a PDF file.
        
        Args:
            pdf_path: Path to the input PDF
            output_path: Path where the numbered PDF should be saved
            start_number: Starting page number (default: 1)
        """
        reader = PdfReader(pdf_path)
        writer = PdfWriter()
        
        for page_num, page in enumerate(reader.pages, start=start_number):
            # Create a watermark with page number
            temp_watermark = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
            temp_watermark_path = temp_watermark.name
            temp_watermark.close()
            
            c = canvas.Canvas(temp_watermark_path, pagesize=letter)
            c.setFont("Helvetica", 10)
            # Position page number at bottom center
            c.drawCentredString(letter[0] / 2, 30, str(page_num))
            c.save()
            
            # Merge watermark with page
            watermark_reader = PdfReader(temp_watermark_path)
            page.merge_page(watermark_reader.pages[0])
            writer.add_page(page)
            
            os.remove(temp_watermark_path)
        
        with open(output_path, 'wb') as output_file:
            writer.write(output_file)
    
    def process_files(
        self,
        file_paths: List[str],
        buffer_pages: List[int],
        output_path: str,
        start_page_number: int = 1,
        pre_converted_pdfs: Optional[Dict[str, str]] = None
    ) -> None:
        """
        Process files: convert Word to PDF, add buffers, merge, and add page numbers.
        
        Args:
            file_paths: List of file paths (PDF or Word)
            buffer_pages: List of buffer page counts (one per file)
            output_path: Path where the final PDF should be saved
            start_page_number: Starting page number (default: 1)
            pre_converted_pdfs: Optional dictionary mapping Word file paths to already-converted PDF paths
        """
        from pathlib import Path
        
        def get_file_extension(file_path: str) -> str:
            """Get file extension."""
            return Path(file_path).suffix.lower()
        processed_pdfs: List[str] = []
        temp_dir = self._create_temp_dir()
        
        if pre_converted_pdfs is None:
            pre_converted_pdfs = {}
        
        try:
            # Process each file
            for file_path, buffer_count in zip(file_paths, buffer_pages):
                file_ext = get_file_extension(file_path)
                
                # Convert Word to PDF if needed (use pre-converted if available)
                if file_ext in ['.docx', '.doc']:
                    if file_path in pre_converted_pdfs:
                        pdf_path = pre_converted_pdfs[file_path]
                    else:
                        pdf_path = self.convert_word_to_pdf(file_path)
                else:
                    pdf_path = file_path
                
                # Add buffer pages if needed
                if buffer_count > 0:
                    buffered_pdf = self.add_buffer_pages(pdf_path, buffer_count)
                    processed_pdfs.append(buffered_pdf)
                else:
                    processed_pdfs.append(pdf_path)
            
            # Merge all PDFs
            merged_pdf = os.path.join(temp_dir, 'merged.pdf')
            self.merge_pdfs(processed_pdfs, merged_pdf)
            
            # Add page numbers
            self.add_page_numbers(merged_pdf, output_path, start_page_number)
            
        finally:
            # Cleanup is handled by the system, but we could add explicit cleanup here
            pass

