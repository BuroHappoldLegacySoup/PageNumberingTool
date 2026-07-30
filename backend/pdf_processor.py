"""
PDF processor module for merging PDFs and adding page numbers.
Handles PDF operations including merging, page numbering, and Word to PDF conversion.
"""

from typing import List, Optional, Union, Dict, Tuple, cast
from pathlib import Path
from dataclasses import dataclass
import tempfile
import os
import platform

from backend.page_number_config import (
    PageNumberSettings,
    DEFAULT_FONT,
    POSITION_ABSOLUTE,
    CM_TO_POINTS,
    origin_offsets_to_bottom_left,
)
from backend.toc_handler import (
    TocInfo,
    adjust_toc_page_numbers,
    apply_toc_bookmarks,
    update_toc_page_in_place,
    _group_words_into_lines,
)

POINTS_TO_CM = 2.54 / 72.0


@dataclass
class FooterLineHint:
    """Y position of the lowest text line in the footer band of a sample page."""

    page_number: int
    page_height_pt: float
    page_width_pt: float
    y_from_bottom_pt: float
    sample_text: str = ""

    @property
    def y_from_bottom_cm(self) -> float:
        return self.y_from_bottom_pt * POINTS_TO_CM

    @property
    def y_from_bottom_percent(self) -> float:
        if self.page_height_pt <= 0:
            return 0.0
        return (self.y_from_bottom_pt / self.page_height_pt) * 100.0

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
        # Cache Word→PDF conversions so we do not launch Word twice for the
        # same file (page count + TOC extraction used to convert separately).
        self._word_pdf_cache: Dict[str, str] = {}

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
        self._word_pdf_cache.clear()

    def detect_footer_last_line(
        self,
        pdf_path: str,
        *,
        bottom_band_fraction: float = 0.20,
    ) -> Optional[FooterLineHint]:
        """
        Estimate the Y of the last footer line from the middle page of a PDF.

        Uses pdfplumber text positions in the bottom band of the page (same
        coordinate space as stamping). Returns None when no footer-like text
        is found.
        """
        try:
            import pdfplumber
        except ImportError:
            return None

        try:
            with pdfplumber.open(pdf_path) as pdf:
                if not pdf.pages:
                    return None
                page_index = len(pdf.pages) // 2
                page = pdf.pages[page_index]
                height = float(page.height or 0.0)
                width = float(page.width or 0.0)
                if height <= 0:
                    return None

                words = page.extract_words() or []
                if not words:
                    return None

                band_top = height * (1.0 - max(0.05, min(0.45, bottom_band_fraction)))
                footer_words = [
                    word for word in words if float(word.get("top", 0.0)) >= band_top
                ]
                if not footer_words:
                    # No text in the footer band — treat as no footer.
                    return None

                lines = _group_words_into_lines(footer_words)
                if not lines:
                    return None

                last_line = max(
                    lines,
                    key=lambda line: max(float(word["bottom"]) for word in line),
                )
                line_bottom = max(float(word["bottom"]) for word in last_line)
                y_from_bottom = max(0.0, height - line_bottom)
                sample = " ".join(
                    word["text"]
                    for word in sorted(last_line, key=lambda item: float(item["x0"]))
                )
                if len(sample) > 48:
                    sample = sample[:45] + "..."

                return FooterLineHint(
                    page_number=page_index + 1,
                    page_height_pt=height,
                    page_width_pt=width,
                    y_from_bottom_pt=y_from_bottom,
                    sample_text=sample,
                )
        except Exception:
            return None

    def estimate_font_descent_pt(self, font_name: str, font_size: float) -> float:
        """
        Estimate how far glyphs extend below the baseline for a font/size.

        Uses ReportLab font metrics when available; falls back to ~22% of size
        (same factor used for the white background box under page numbers).
        """
        size = max(0.1, float(font_size))
        fallback = size * 0.22
        try:
            rl_font = self._register_reportlab_font(font_name or DEFAULT_FONT)
            from reportlab.pdfbase import pdfmetrics

            font = pdfmetrics.getFont(rl_font)
            face = getattr(font, "face", None)
            if face is None:
                return fallback
            raw_descent = float(getattr(face, "descent", 0.0) or 0.0)
            if raw_descent == 0.0:
                return fallback
            # TrueType faces use a 1000-unit em square; descent is usually negative.
            return abs(raw_descent) * size / 1000.0
        except Exception:
            return fallback

    def estimate_baseline_y_from_bottom_pt(
        self,
        glyph_bottom_y_from_bottom_pt: float,
        font_name: str,
        font_size: float,
    ) -> float:
        """
        Convert a glyph-box bottom Y (from page bottom) into a baseline Y.

        Page numbers are drawn on the baseline, so this is the Y to use for
        aligning with the measured footer line when using ``font_name``/size.
        """
        descent = self.estimate_font_descent_pt(font_name, font_size)
        return max(0.0, float(glyph_bottom_y_from_bottom_pt) + descent)
    
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
                        # Word is running - don't force-kill (user may have docs open)
                        pass
                except Exception:
                    pass  # Ignore errors in cleanup attempt
        except Exception:
            pass  # Ignore all cleanup errors
    
    def _convert_word_via_com(self, word_abs_path: str, output_abs_path: str) -> None:
        """
        Convert Word → PDF via Word COM directly (no tqdm / stdout).

        Avoids docx2pdf's tqdm progress bar, which crashes in --noconsole
        PyInstaller builds where sys.stdout is None ('NoneType' has no
        attribute 'write'). Also copies cloud/SharePoint files to a local
        temp path first so Word can open them reliably.
        """
        import shutil
        import time

        try:
            import win32com.client
            import pythoncom
        except ImportError as exc:
            raise ImportError(
                "pywin32 is required for Word to PDF conversion. "
                "Please install it with: pip install pywin32"
            ) from exc

        if not os.path.isfile(word_abs_path):
            raise FileNotFoundError(f"Word document not found: {word_abs_path}")

        temp_dir = self._create_temp_dir()
        # Local copy avoids OneDrive/SharePoint placeholder / lock issues.
        local_docx = os.path.join(
            temp_dir, f"_convert_{os.getpid()}_{Path(word_abs_path).name}"
        )
        shutil.copy2(word_abs_path, local_docx)

        pythoncom.CoInitialize()
        word_app = None
        doc = None
        try:
            word_app = win32com.client.DispatchEx("Word.Application")
            word_app.Visible = False
            word_app.DisplayAlerts = 0

            # ConfirmConversions=False, ReadOnly=True, AddToRecentFiles=False
            doc = word_app.Documents.Open(
                local_docx,
                False,
                True,
                False,
            )
            # ExportAsFixedFormat (not SaveAs) so heading-based PDF bookmarks
            # are created — same as Word's "Create bookmarks using: Headings".
            # 17 = wdExportFormatPDF
            # 1  = wdExportCreateHeadingBookmarks
            doc.ExportAsFixedFormat(
                OutputFileName=output_abs_path,
                ExportFormat=17,
                OpenAfterExport=False,
                OptimizeFor=0,  # wdExportOptimizeForPrint
                Range=0,  # wdExportAllDocument
                Item=0,  # wdExportDocumentContent
                IncludeDocProps=True,
                KeepIRM=True,
                CreateBookmarks=1,  # wdExportCreateHeadingBookmarks
                DocStructureTags=True,
                BitmapMissingFonts=True,
                UseISO19005_1=False,
            )
        finally:
            if doc is not None:
                try:
                    doc.Close(False)
                except Exception:
                    pass
            if word_app is not None:
                try:
                    word_app.Quit()
                except Exception:
                    pass
                # Give Word a moment to release the file lock on Quit.
                time.sleep(0.3)
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
            try:
                if os.path.exists(local_docx):
                    os.remove(local_docx)
            except OSError:
                pass

    def convert_word_to_pdf(self, word_path: str, output_path: Optional[str] = None) -> str:
        """
        Convert a Word document to PDF via Microsoft Word COM automation.

        Results are cached by absolute Word path so page-count and TOC extraction
        reuse one Word launch instead of converting the same file twice.

        Args:
            word_path: Path to the Word document (.docx or .doc)
            output_path: Optional output path for the PDF

        Returns:
            Path to the converted PDF file
        """
        word_abs_path = os.path.abspath(word_path)
        cached = self._word_pdf_cache.get(word_abs_path)
        if (
            output_path is None
            and cached
            and os.path.exists(cached)
            and os.path.getsize(cached) > 0
        ):
            return cached

        if output_path is None:
            temp_dir = self._create_temp_dir()
            output_path = os.path.join(temp_dir, Path(word_path).stem + ".pdf")

        output_abs_path = os.path.abspath(output_path)
        max_retries = 3
        last_error: Optional[BaseException] = None

        for attempt in range(max_retries):
            try:
                # Remove a partial PDF from a previous failed attempt.
                if os.path.exists(output_abs_path):
                    try:
                        os.remove(output_abs_path)
                    except OSError:
                        pass

                self._convert_word_via_com(word_abs_path, output_abs_path)

                if not os.path.exists(output_abs_path) or os.path.getsize(output_abs_path) == 0:
                    raise FileNotFoundError(
                        f"PDF conversion failed: {output_abs_path} not created"
                    )
                break

            except Exception as e:
                last_error = e
                error_msg = str(e).lower()

                if os.path.exists(output_abs_path) and os.path.getsize(output_abs_path) > 0:
                    break

                retryable = any(
                    token in error_msg
                    for token in ("quit", "com", "application", "rpc", "busy", "locked")
                )
                if retryable and attempt < max_retries - 1:
                    try:
                        self._cleanup_word_processes()
                    except Exception:
                        pass
                    import time
                    time.sleep(1)
                    continue

                if attempt == max_retries - 1:
                    if os.path.exists(output_abs_path) and os.path.getsize(output_abs_path) > 0:
                        break

                    if retryable:
                        raise RuntimeError(
                            f"Failed to convert Word document to PDF after {max_retries} attempts.\n"
                            f"Error: {e}\n\n"
                            "This is often caused by Word COM automation issues. Try:\n"
                            "1. Close all Word windows (check Task Manager for WINWORD.EXE)\n"
                            "2. Make sure the document is not corrupted or password-protected\n"
                            "3. If the file is on SharePoint/OneDrive, download a local copy first"
                        ) from e
                    raise RuntimeError(
                        f"Failed to convert Word document to PDF: {e}\n"
                        "Make sure Microsoft Word is installed and the document is not open in Word."
                    ) from e

        if not os.path.exists(output_abs_path) or os.path.getsize(output_abs_path) == 0:
            if last_error:
                raise RuntimeError(
                    f"Failed to convert Word document to PDF after {max_retries} attempts.\n"
                    f"Last error: {last_error}\n\n"
                    "The PDF file was not created. Please check:\n"
                    "1. Microsoft Word is installed and working\n"
                    "2. The document is not corrupted\n"
                    "3. You have write permissions to the output directory"
                )
            raise RuntimeError("PDF conversion failed - no PDF file was created.")

        self._word_pdf_cache[word_abs_path] = output_abs_path
        return output_abs_path
    
    def get_page_count(self, file_path: str, convert_word: bool = True) -> int:
        """
        Get the number of pages in a PDF or Word document.
        For Word documents, converts to PDF (cached) to get an accurate page count.
        
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
                # Reuse cached conversion when available (do not delete — TOC /
                # processing need the same PDF).
                temp_pdf = self.convert_word_to_pdf(file_path)
                reader = PdfReader(temp_pdf)
                return len(reader.pages)
            except Exception:
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
    
    def extract_pdf_pages(
        self,
        pdf_path: str,
        page_numbers: List[int],
        output_path: Optional[str] = None,
    ) -> str:
        """
        Extract 1-based page numbers from a PDF into a new PDF (order preserved).

        Args:
            pdf_path: Source PDF
            page_numbers: 1-based page numbers to keep, in desired order
            output_path: Optional destination path

        Returns:
            Path to the extracted PDF
        """
        if not page_numbers:
            raise ValueError("No pages to extract")

        reader = PdfReader(pdf_path, strict=False)
        total = len(reader.pages)
        writer = PdfWriter()
        for page_number in page_numbers:
            if page_number < 1 or page_number > total:
                raise ValueError(
                    f"Page {page_number} is outside 1–{total} in {Path(pdf_path).name}"
                )
            writer.add_page(reader.pages[page_number - 1])

        if output_path is None:
            temp_dir = self._create_temp_dir()
            output_path = os.path.join(
                temp_dir,
                f"extract_{Path(pdf_path).stem}_{page_numbers[0]}_{page_numbers[-1]}.pdf",
            )
        with open(output_path, "wb") as handle:
            writer.write(handle)
        return output_path

    def _repair_pdf(self, input_path: str, output_path: str) -> None:
        """
        Re-write a PDF through PdfWriter to rebuild a consistent object table.

        Helps avoid broken cross-document references after merging multiple sources.
        """
        reader = PdfReader(input_path, strict=False)
        writer = PdfWriter()
        writer.append_pages_from_reader(reader)
        with open(output_path, "wb") as output_file:
            writer.write(output_file)

    def merge_main_with_insertions(
        self,
        main_pdf_path: str,
        insertions: List[Tuple[int, str]],
        output_path: str
    ) -> None:
        """
        Merge a main PDF with other PDFs inserted after specific main-document pages.

        Opens the main PDF and inserts foreign pages into its page tree so the
        original page objects keep their identity. That preserves TOC hyperlinks,
        named destinations, and bookmarks: they still point at the same chapter
        pages even after insertions shift page indices.

        Insertion page numbers refer to the original main document (1-based).
        "After page N" keeps main pages 1..N, then the inserted file, then continues
        with the remaining main pages.

        Args:
            main_pdf_path: Path to the main PDF
            insertions: List of (after_page, pdf_path) pairs; after_page is 1-based
            output_path: Path where the merged PDF should be saved
        """
        try:
            import pikepdf
        except ImportError as exc:
            raise ImportError(
                "pikepdf is required to assemble documents while preserving TOC links. "
                "Install it with: pip install pikepdf"
            ) from exc

        with pikepdf.Pdf.open(main_pdf_path) as main_pdf:
            main_page_count = len(main_pdf.pages)

            for after_page, _ in insertions:
                if after_page < 0 or after_page > main_page_count:
                    raise ValueError(
                        f"Insert position must be between 0 and {main_page_count} "
                        f"(main document has {main_page_count} pages)."
                    )

            # Insert from the end so earlier insert indices stay valid.
            for after_page, insert_pdf_path in reversed(
                sorted(insertions, key=lambda item: item[0])
            ):
                with pikepdf.Pdf.open(insert_pdf_path) as insert_pdf:
                    for offset, page in enumerate(insert_pdf.pages):
                        main_pdf.pages.insert(after_page + offset, page)

            main_pdf.save(output_path)
    
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

        X/Y offsets are measured inward from ``settings.position_origin``.
        """
        display_w, display_h = self._get_display_dimensions(
            width, height, page_rotation
        )
        if settings.position_mode == POSITION_ABSOLUTE:
            offset_x = settings.x_cm * CM_TO_POINTS
            offset_y = settings.y_cm * CM_TO_POINTS
        else:
            offset_x = display_w * settings.x_percent / 100.0
            offset_y = display_h * settings.y_percent / 100.0
        visual_x, visual_y = origin_offsets_to_bottom_left(
            offset_x,
            offset_y,
            display_w,
            display_h,
            settings.position_origin,
        )
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
        
        Offsets are measured inward from ``settings.position_origin``, then mapped
        to viewer coordinates (bottom-left, x right, y up) after /Rotate. Text is
        rotated so it reads upright on screen. An optional filled white rectangle
        may be drawn behind the text.
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
            stamp_page = PdfReader(temp_watermark_path, strict=False).pages[0]
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
        preserve_links: bool = True,
    ) -> None:
        """
        Add page numbers to a PDF file.

        Each page is numbered using that page's media box and /Rotate. Offsets are
        measured from the configured origin corner and mapped to viewer coordinates.
        Numbers are merged as the last content layer so they appear above page content.

        When preserve_links is True (default), stamps are applied with pikepdf
        overlays so TOC hyperlinks, named destinations, and bookmarks survive.

        Args:
            pdf_path: Path to the input PDF
            output_path: Path where the numbered PDF should be saved
            start_number: Starting page number (default: 1)
            settings: Page number formatting/position settings
            preserve_links: Keep interactive links and bookmarks (default: True)
        """
        if settings is None:
            settings = PageNumberSettings()

        if preserve_links:
            self._add_page_numbers_preserving_links(
                pdf_path, output_path, start_number, settings
            )
            return

        reader = PdfReader(pdf_path, strict=False)
        writer = PdfWriter()

        for page_num, page in enumerate(reader.pages, start=start_number):
            writer.add_page(page)
            output_page = writer.pages[-1]
            self._apply_page_number_overlay(output_page, page_num, settings)

        with open(output_path, "wb") as output_file:
            writer.write(output_file)

    def _add_page_numbers_preserving_links(
        self,
        pdf_path: str,
        output_path: str,
        start_number: int,
        settings: PageNumberSettings,
    ) -> None:
        """Stamp page numbers with pikepdf overlays without rewriting the page tree."""
        try:
            import pikepdf
        except ImportError as exc:
            raise ImportError(
                "pikepdf is required to add page numbers while preserving TOC links. "
                "Install it with: pip install pikepdf"
            ) from exc

        with pikepdf.Pdf.open(pdf_path) as pdf:
            for page_index, page in enumerate(pdf.pages):
                page_num = start_number + page_index
                mediabox = page.mediabox
                width = float(mediabox[2] - mediabox[0])
                height = float(mediabox[3] - mediabox[1])
                rotation = int(page.get("/Rotate", 0) or 0) % 360

                temp_watermark_path = tempfile.NamedTemporaryFile(
                    delete=False, suffix=".pdf"
                ).name
                try:
                    c = canvas.Canvas(temp_watermark_path, pagesize=(width, height))
                    text = settings.format_number(page_num)
                    self._draw_page_number_on_canvas(
                        c, text, width, height, settings, rotation
                    )
                    c.save()

                    with pikepdf.Pdf.open(temp_watermark_path) as stamp_pdf:
                        page.add_overlay(
                            stamp_pdf.pages[0],
                            shrink=False,
                            expand=False,
                        )
                finally:
                    if os.path.exists(temp_watermark_path):
                        os.remove(temp_watermark_path)

            pdf.save(output_path)
    
    def _insertion_page_counts(
        self,
        insertions: List[Tuple[int, str]],
    ) -> List[Tuple[int, int]]:
        """Return (after_page, inserted_page_count) for each insertion."""
        counts: List[Tuple[int, int]] = []
        for after_page, pdf_path in insertions:
            reader = PdfReader(pdf_path, strict=False)
            counts.append((after_page, len(reader.pages)))
        return counts

    def process_files_with_main(
        self,
        main_file_path: str,
        insertions: List[Tuple[str, int, str]],
        buffer_pages: Dict[str, int],
        output_path: str,
        start_page_number: int = 1,
        pre_converted_pdfs: Optional[Dict[str, str]] = None,
        page_number_settings: Optional[PageNumberSettings] = None,
        toc_info: Optional[TocInfo] = None,
    ) -> None:
        """
        Process files using a main document with other files inserted at page boundaries.
        
        Args:
            main_file_path: Path to the main document (PDF or Word)
            insertions: List of (file_path, after_page, pages_spec). ``pages_spec``
                empty means all pages; otherwise a range/list like ``1-4`` or ``5,6``.
            buffer_pages: Map of file_path to blank pages added after that file's content
            output_path: Path where the final PDF should be saved
            start_page_number: Starting page number for footer numbering
            pre_converted_pdfs: Optional map of Word paths to already-converted PDF paths
        """
        from backend.page_spec import parse_pages_spec

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
        for file_path, after_page, pages_spec in insertions:
            source_pdf = resolve_pdf_path(file_path)
            reader = PdfReader(source_pdf, strict=False)
            page_numbers = parse_pages_spec(pages_spec, len(reader.pages))
            if len(page_numbers) == len(reader.pages) and not (pages_spec or "").strip():
                insert_pdf = source_pdf
            elif len(page_numbers) == len(reader.pages) and set(page_numbers) == set(
                range(1, len(reader.pages) + 1)
            ):
                insert_pdf = source_pdf
            else:
                insert_pdf = self.extract_pdf_pages(
                    source_pdf,
                    page_numbers,
                    os.path.join(
                        temp_dir,
                        f"seg_{Path(file_path).stem}_{after_page}_{page_numbers[0]}.pdf",
                    ),
                )
            insert_pdf = apply_buffer(insert_pdf, file_path)
            pdf_insertions.append((after_page, insert_pdf))
        
        merged_pdf = os.path.join(temp_dir, 'merged_main.pdf')
        self.merge_main_with_insertions(main_pdf, pdf_insertions, merged_pdf)

        # Keep the merged file as-is (no PyPDF2 rewrite) so TOC links, named
        # destinations, and bookmarks continue to resolve to the same pages.
        numbered_input = merged_pdf
        if toc_info is not None and toc_info.can_update_in_place:
            updated_pdf = os.path.join(temp_dir, 'merged_main_toc.pdf')
            insertion_counts = self._insertion_page_counts(pdf_insertions)
            try:
                if update_toc_page_in_place(
                    merged_pdf,
                    toc_info,
                    insertion_counts,
                    updated_pdf,
                    temp_dir,
                ):
                    numbered_input = updated_pdf
            except Exception:
                numbered_input = merged_pdf

        self.add_page_numbers(
            numbered_input,
            output_path,
            start_page_number,
            page_number_settings,
            preserve_links=True,
        )

        # Rebuild the PDF outline from the TOC so the sidebar bookmarks match
        # TOC hierarchy and point at post-insertion page positions.
        if toc_info is not None and toc_info.entries:
            insertion_counts = self._insertion_page_counts(pdf_insertions)
            adjusted_entries = adjust_toc_page_numbers(
                toc_info.entries, insertion_counts
            )
            try:
                apply_toc_bookmarks(output_path, adjusted_entries)
            except Exception:
                pass

