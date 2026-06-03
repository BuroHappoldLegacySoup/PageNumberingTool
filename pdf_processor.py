"""
PDF processor module for merging PDFs and adding page numbers.
Handles PDF operations including merging, page numbering, and Word to PDF conversion.
"""

from typing import List, Optional, Union, Dict, Tuple, cast
from pathlib import Path
import tempfile
import os
import platform

from page_number_config import (
    PageNumberSettings,
    DEFAULT_FONT,
    POSITION_ABSOLUTE,
    CM_TO_POINTS,
)

try:
    from PyPDF2 import PdfReader, PdfWriter, PageObject, Transformation
    from PyPDF2.constants import PageAttributes as PG
    from PyPDF2.constants import Ressources as RES
    from PyPDF2.generic import ArrayObject, ContentStream, DictionaryObject, NameObject
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.colors import white
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
        self._registered_fonts: Dict[str, str] = {}

    def _register_reportlab_font(self, font_name: str) -> str:
        """
        Register a TrueType font with ReportLab when possible.

        Returns:
            ReportLab font name to pass to setFont (may differ from display name).
        """
        if font_name in self._registered_fonts:
            return self._registered_fonts[font_name]

        builtin = {
            "Helvetica": "Helvetica",
            "Times New Roman": "Times-Roman",
            "Courier New": "Courier",
        }
        if font_name in builtin:
            self._registered_fonts[font_name] = builtin[font_name]
            return builtin[font_name]

        if font_name == "Helvetica":
            self._registered_fonts[font_name] = "Helvetica"
            return "Helvetica"

        windows_font_files = {
            "Arial": "arial.ttf",
            "Calibri": "calibri.ttf",
            "Cambria": "cambria.ttc",
            "Georgia": "georgia.ttf",
            "Segoe UI": "segoeui.ttf",
            "Tahoma": "tahoma.ttf",
            "Trebuchet MS": "trebuc.ttf",
            "Verdana": "verdana.ttf",
        }
        filename = windows_font_files.get(font_name)
        if filename and platform.system() == "Windows":
            font_path = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", filename)
            if os.path.isfile(font_path):
                try:
                    from reportlab.pdfbase import pdfmetrics
                    from reportlab.pdfbase.ttfonts import TTFont

                    internal_name = font_name.replace(" ", "")
                    if internal_name not in pdfmetrics.getRegisteredFontNames():
                        pdfmetrics.registerFont(TTFont(internal_name, font_path))
                    self._registered_fonts[font_name] = internal_name
                    return internal_name
                except Exception:
                    pass

        self._registered_fonts[font_name] = "Helvetica"
        return "Helvetica"
    
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
    
    def merge_main_with_insertions(
        self,
        main_pdf_path: str,
        insertions: List[Tuple[int, str]],
        output_path: str
    ) -> None:
        """
        Merge a main PDF with other PDFs inserted after specific main-document pages.
        
        Insertion page numbers refer to the original main document (1-based).
        "After page N" keeps main pages 1..N, then the inserted file, then continues
        with the remaining main pages.
        
        Args:
            main_pdf_path: Path to the main PDF
            insertions: List of (after_page, pdf_path) pairs; after_page is 1-based
            output_path: Path where the merged PDF should be saved
        """
        main_reader = PdfReader(main_pdf_path)
        main_pages = main_reader.pages
        main_page_count = len(main_pages)
        
        sorted_insertions = sorted(insertions, key=lambda item: item[0])
        
        for after_page, _ in sorted_insertions:
            if after_page < 0 or after_page > main_page_count:
                raise ValueError(
                    f"Insert position must be between 0 and {main_page_count} "
                    f"(main document has {main_page_count} pages)."
                )
        
        writer = PdfWriter()
        prev_end = 0
        
        for after_page, insert_pdf_path in sorted_insertions:
            for page_index in range(prev_end, after_page):
                writer.add_page(main_pages[page_index])
            
            insert_reader = PdfReader(insert_pdf_path)
            for page in insert_reader.pages:
                writer.add_page(page)
            
            prev_end = after_page
        
        for page_index in range(prev_end, main_page_count):
            writer.add_page(main_pages[page_index])
        
        with open(output_path, 'wb') as output_file:
            writer.write(output_file)
    
    def _get_page_rotation(self, page) -> int:
        """
        Get the page rotation in degrees (0, 90, 180, or 270).
        
        Args:
            page: PyPDF2 page object
            
        Returns:
            Rotation in degrees, normalized to 0-359
        """
        rotation = getattr(page, 'rotation', None)
        if rotation is None:
            rotation = page.get('/Rotate', 0)
        if rotation is None:
            return 0
        return int(rotation) % 360
    
    def _get_page_mediabox_rect(self, page) -> Tuple[float, float, float, float]:
        """
        Get page media box origin and size in PDF points.
        
        Args:
            page: PyPDF2 page object
            
        Returns:
            Tuple of (left, bottom, width, height) in points
        """
        mediabox = page.mediabox
        return (
            float(mediabox.left),
            float(mediabox.bottom),
            float(mediabox.width),
            float(mediabox.height),
        )
    
    def _get_page_mediabox_size(self, page) -> Tuple[float, float]:
        """
        Get page width and height in PDF points from the media box.
        
        Args:
            page: PyPDF2 page object
            
        Returns:
            Tuple of (width, height) in points
        """
        _, _, width, height = self._get_page_mediabox_rect(page)
        return width, height
    
    def _get_display_dimensions(
        self, width: float, height: float, page_rotation: int
    ) -> Tuple[float, float]:
        """
        Width and height of the page as shown in a PDF viewer (after /Rotate).
        """
        if page_rotation in (90, 270):
            return height, width
        return width, height
    
    def _visual_to_user_point(
        self,
        visual_x: float,
        visual_y: float,
        width: float,
        height: float,
        page_rotation: int,
    ) -> Tuple[float, float]:
        """
        Map a point from viewer coordinates to PDF user space.
        
        Viewer coords use bottom-left of the displayed page as origin, with x
        right and y up — matching how position presets are described in the UI.
        """
        rotation = page_rotation % 360
        if rotation == 0:
            return visual_x, visual_y
        if rotation == 90:
            # /Rotate 90: map viewer x across user y, viewer y across user x (from top)
            return width - visual_y, visual_x
        if rotation == 180:
            return width - visual_x, height - visual_y
        if rotation == 270:
            return visual_y, height - visual_x
        return visual_x, visual_y
    
    def _text_rotation_for_page(self, page_rotation: int) -> int:
        """
        Degrees to rotate stamp text in user space so it reads upright on screen.
        """
        rotation = page_rotation % 360
        if rotation == 90:
            return 90
        if rotation == 180:
            return 180
        if rotation == 270:
            return -90
        return 0
    
    def _resolve_page_number_position(
        self,
        width: float,
        height: float,
        page_rotation: int,
        settings: PageNumberSettings,
    ) -> Tuple[float, float]:
        """
        Resolve preset/absolute position to PDF user-space coordinates.
        """
        display_w, display_h = self._get_display_dimensions(
            width, height, page_rotation
        )
        if settings.position_mode == POSITION_ABSOLUTE:
            visual_x = settings.x_cm * CM_TO_POINTS
            visual_y = settings.y_cm * CM_TO_POINTS
        else:
            visual_x = display_w * settings.x_percent / 100.0
            visual_y = display_h * settings.y_percent / 100.0
        return self._visual_to_user_point(
            visual_x, visual_y, width, height, page_rotation
        )
    
    def _merge_stamp_on_top(
        self,
        base_page: PageObject,
        stamp_page: PageObject,
        translation: Optional[Transformation] = None,
    ) -> None:
        """
        Merge stamp_page onto base_page as the topmost content layer.
        
        Unlike merge_page, this does not apply a trimbox clip to the stamp, which
        can hide page numbers when boxes differ between the stamp and target page.
        The stamp content stream is appended after the page content so it paints on top.
        
        Args:
            base_page: Page to receive the stamp
            stamp_page: Single-page overlay (page number)
            translation: Optional translation into the base page coordinate system
        """
        new_resources = DictionaryObject()
        rename: Dict = {}
        
        try:
            original_resources = cast(
                DictionaryObject, base_page[PG.RESOURCES].get_object()
            )
        except KeyError:
            original_resources = DictionaryObject()
        try:
            stamp_resources = cast(
                DictionaryObject, stamp_page[PG.RESOURCES].get_object()
            )
        except KeyError:
            stamp_resources = DictionaryObject()
        
        new_annots = ArrayObject()
        for pdf_page in (base_page, stamp_page):
            if PG.ANNOTS in pdf_page:
                annots = pdf_page[PG.ANNOTS]
                if isinstance(annots, ArrayObject):
                    for ref in annots:
                        new_annots.append(ref)
        
        for res in (
            RES.EXT_G_STATE,
            RES.FONT,
            RES.XOBJECT,
            RES.COLOR_SPACE,
            RES.PATTERN,
            RES.SHADING,
            RES.PROPERTIES,
        ):
            new, newrename = PageObject._merge_resources(
                original_resources, stamp_resources, res
            )
            if new:
                new_resources[NameObject(res)] = new
                rename.update(newrename)
        
        new_resources[NameObject(RES.PROC_SET)] = ArrayObject(
            frozenset(
                original_resources.get(RES.PROC_SET, ArrayObject()).get_object()
            ).union(
                frozenset(stamp_resources.get(RES.PROC_SET, ArrayObject()).get_object())
            )
        )
        
        new_content_array = ArrayObject()
        
        original_content = base_page.get_contents()
        if original_content is not None:
            new_content_array.append(
                PageObject._push_pop_gs(original_content, base_page.pdf)
            )
        
        stamp_content = stamp_page.get_contents()
        if stamp_content is not None:
            stamp_stream = ContentStream(stamp_content, base_page.pdf)
            if translation is not None:
                stamp_stream = PageObject._add_transformation_matrix(
                    stamp_stream, base_page.pdf, translation.ctm
                )
            stamp_stream = PageObject._content_stream_rename(
                stamp_stream, rename, base_page.pdf
            )
            stamp_stream = PageObject._push_pop_gs(stamp_stream, base_page.pdf)
            new_content_array.append(stamp_stream)
        
        base_page[NameObject(PG.CONTENTS)] = ContentStream(
            new_content_array, base_page.pdf
        )
        base_page[NameObject(PG.RESOURCES)] = new_resources
        if len(new_annots) > 0:
            base_page[NameObject(PG.ANNOTS)] = new_annots
    
    def _draw_page_number_on_canvas(
        self,
        c: canvas.Canvas,
        text: str,
        width: float,
        height: float,
        settings: PageNumberSettings,
        page_rotation: int = 0,
    ) -> None:
        """
        Draw page number text at the configured position.
        
        Position is interpreted from the bottom-left of the page as displayed in
        a viewer (after /Rotate). Text is rotated so it reads upright on screen.
        An optional filled white rectangle may be drawn behind the text.
        """
        rl_font = self._register_reportlab_font(settings.font_name or DEFAULT_FONT)
        font_size = max(0.1, settings.font_size)
        c.setFont(rl_font, font_size)

        x_pt, y_pt = self._resolve_page_number_position(
            width, height, page_rotation, settings
        )
        anchor = settings.text_anchor
        text_rotation = self._text_rotation_for_page(page_rotation)

        pad_x = 4.0
        pad_y = 3.0
        text_width = c.stringWidth(text, rl_font, font_size)
        box_height = font_size * 1.15 + 2 * pad_y
        box_width = text_width + 2 * pad_x

        c.saveState()
        c.translate(x_pt, y_pt)
        if text_rotation:
            c.rotate(text_rotation)

        box_bottom = -font_size * 0.22 - pad_y
        if anchor == "center":
            box_left = -text_width / 2.0 - pad_x
        elif anchor == "right":
            box_left = -text_width - pad_x
        else:
            box_left = -pad_x

        if settings.use_white_background:
            c.setFillColor(white)
            c.setStrokeColor(white)
            c.rect(box_left, box_bottom, box_width, box_height, fill=1, stroke=0)

        r, g, b = settings.font_color_rgb
        c.setFillColorRGB(r / 255.0, g / 255.0, b / 255.0)
        if anchor == "center":
            c.drawCentredString(0, 0, text)
        elif anchor == "right":
            c.drawRightString(0, 0, text)
        else:
            c.drawString(0, 0, text)
        c.restoreState()

    def _create_page_number_watermark(
        self, page_num: int, page, settings: PageNumberSettings
    ) -> str:
        """
        Create a single-page PDF watermark with a page number for the given page.
        
        Args:
            page_num: Page number to render
            page: PyPDF2 page object used for size and rotation
            
        Returns:
            Path to the temporary watermark PDF file
        """
        width, height = self._get_page_mediabox_size(page)
        page_rotation = self._get_page_rotation(page)
        
        temp_watermark = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        temp_watermark_path = temp_watermark.name
        temp_watermark.close()
        
        c = canvas.Canvas(temp_watermark_path, pagesize=(width, height))
        text = settings.format_number(page_num)
        self._draw_page_number_on_canvas(
            c, text, width, height, settings, page_rotation
        )
        c.save()
        
        return temp_watermark_path
    
    def _apply_page_number_overlay(
        self, page: PageObject, page_num: int, settings: PageNumberSettings
    ) -> None:
        """
        Stamp a page number onto a page as the topmost content layer.
        
        Args:
            page: Target page (must already belong to a PdfWriter)
            page_num: Page number to display
        """
        temp_watermark_path = self._create_page_number_watermark(page_num, page, settings)
        
        try:
            stamp_page = PdfReader(temp_watermark_path).pages[0]
            left, bottom, _, _ = self._get_page_mediabox_rect(page)
            translation = None
            if left != 0.0 or bottom != 0.0:
                translation = Transformation().translate(tx=left, ty=bottom)
            self._merge_stamp_on_top(page, stamp_page, translation)
        finally:
            if os.path.exists(temp_watermark_path):
                os.remove(temp_watermark_path)
    
    def add_page_numbers(
        self,
        pdf_path: str,
        output_path: str,
        start_number: int = 1,
        settings: Optional[PageNumberSettings] = None,
    ) -> None:
        """
        Add page numbers to a PDF file.
        
        Each page is numbered using that page's media box and /Rotate so preset
        positions match the bottom-left of the page as shown in a PDF viewer.
        Numbers are merged as the last content layer so they appear above page content.
        
        Args:
            pdf_path: Path to the input PDF
            output_path: Path where the numbered PDF should be saved
            start_number: Starting page number (default: 1)
        """
        if settings is None:
            settings = PageNumberSettings()

        reader = PdfReader(pdf_path)
        writer = PdfWriter()
        
        for page_num, page in enumerate(reader.pages, start=start_number):
            writer.add_page(page)
            output_page = writer.pages[-1]
            self._apply_page_number_overlay(output_page, page_num, settings)
        
        with open(output_path, 'wb') as output_file:
            writer.write(output_file)
    
    def process_files(
        self,
        file_paths: List[str],
        buffer_pages: List[int],
        output_path: str,
        start_page_number: int = 1,
        pre_converted_pdfs: Optional[Dict[str, str]] = None,
        page_number_settings: Optional[PageNumberSettings] = None,
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
            self.add_page_numbers(
                merged_pdf, output_path, start_page_number, page_number_settings
            )
            
        finally:
            # Cleanup is handled by the system, but we could add explicit cleanup here
            pass
    
    def process_files_with_main(
        self,
        main_file_path: str,
        insertions: List[Tuple[str, int]],
        buffer_pages: Dict[str, int],
        output_path: str,
        start_page_number: int = 1,
        pre_converted_pdfs: Optional[Dict[str, str]] = None,
        page_number_settings: Optional[PageNumberSettings] = None,
    ) -> None:
        """
        Process files using a main document with other files inserted at page boundaries.
        
        Args:
            main_file_path: Path to the main document (PDF or Word)
            insertions: List of (file_path, after_page) for files to insert into main
            buffer_pages: Map of file_path to blank pages added after that file's content
            output_path: Path where the final PDF should be saved
            start_page_number: Starting page number for footer numbering
            pre_converted_pdfs: Optional map of Word paths to already-converted PDF paths
        """
        from pathlib import Path
        
        def get_file_extension(file_path: str) -> str:
            return Path(file_path).suffix.lower()
        
        def resolve_pdf_path(file_path: str) -> str:
            file_ext = get_file_extension(file_path)
            if file_ext in ['.docx', '.doc']:
                if pre_converted_pdfs and file_path in pre_converted_pdfs:
                    return pre_converted_pdfs[file_path]
                return self.convert_word_to_pdf(file_path)
            return file_path
        
        def apply_buffer(pdf_path: str, file_path: str) -> str:
            buffer_count = buffer_pages.get(file_path, 0)
            if buffer_count > 0:
                return self.add_buffer_pages(pdf_path, buffer_count)
            return pdf_path
        
        temp_dir = self._create_temp_dir()
        
        if pre_converted_pdfs is None:
            pre_converted_pdfs = {}
        
        main_pdf = resolve_pdf_path(main_file_path)
        
        pdf_insertions: List[Tuple[int, str]] = []
        for file_path, after_page in insertions:
            insert_pdf = apply_buffer(resolve_pdf_path(file_path), file_path)
            pdf_insertions.append((after_page, insert_pdf))
        
        merged_pdf = os.path.join(temp_dir, 'merged_main.pdf')
        self.merge_main_with_insertions(main_pdf, pdf_insertions, merged_pdf)
        self.add_page_numbers(
            merged_pdf, output_path, start_page_number, page_number_settings
        )

