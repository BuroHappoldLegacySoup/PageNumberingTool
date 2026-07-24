"""
Table of contents extraction and in-place update for the Page Numbering Tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Callable, List, Optional, Tuple

try:
    import pdfplumber
    from PyPDF2 import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas
    from reportlab.lib.colors import white
    import docx
except ImportError:
    pass

TOC_TITLE_PATTERNS = (
    "table of contents",
    "contents",
    "inhaltsverzeichnis",
    "table des matières",
    "inhalt",
)

# Exact / short TOC heading lines (avoid matching body text that merely contains "Inhalt")
TOC_TITLE_EXACT = frozenset(
    {
        "inhalt",
        "inhalte",
        "contents",
        "table of contents",
        "inhaltsverzeichnis",
        "table des matières",
    }
)

# Supports plain "12" and hyphen-wrapped " - 12 - " (common in German Word TOCs)
TOC_ENTRY_RE = re.compile(
    r"^(\s*)(.+?)\s*(?:[.\u00b7·\t]{2,}|\s{3,})\s*(?:[\-\u2013]\s*)?(\d+)\s*(?:[\-\u2013]\s*)?$"
)

PAGE_NUMBER_WORD_RE = re.compile(r"^\d+$")
PAGE_NUMBER_TOKEN_RE = re.compile(r"^(?:[\-\u2013]\s*)?(\d+)(?:\s*[\-\u2013])?$")
HYPHEN_ONLY_RE = re.compile(r"^[\-\u2013]+$")


def _parse_page_number_token(text: str) -> Optional[int]:
    """Parse a TOC page-number token such as '12', '- 12 -', or '-12-'."""
    cleaned = (text or "").strip()
    if not cleaned or HYPHEN_ONLY_RE.fullmatch(cleaned):
        return None
    match = PAGE_NUMBER_TOKEN_RE.fullmatch(cleaned)
    if match:
        return int(match.group(1))
    if PAGE_NUMBER_WORD_RE.fullmatch(cleaned):
        return int(cleaned)
    return None


def _is_toc_title_text(text: str) -> Optional[str]:
    """Return the TOC heading if this page/line text starts with a TOC title."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return None
    for line in lines[:3]:
        lower = line.lower().strip()
        if lower in TOC_TITLE_EXACT:
            return line
        if any(pattern in lower for pattern in TOC_TITLE_PATTERNS):
            # Require a short heading line so body mentions are ignored.
            if len(lower) <= 40:
                return line
    return None

CHAPTER_NUMBER_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$")


def parse_chapter_parts(title: str) -> Tuple[Optional[str], str]:
    """
    Split a TOC title into an optional chapter/subchapter number and display name.

    Examples:
        "1 Introduction" -> ("1", "Introduction")
        "1.2.3 Methods" -> ("1.2.3", "Methods")
        "Appendix" -> (None, "Appendix")
    """
    stripped = title.strip()
    match = CHAPTER_NUMBER_RE.match(stripped)
    if match:
        return match.group(1), match.group(2).strip()
    return None, stripped


@dataclass
class TocEntry:
    """A single table-of-contents entry."""

    title: str
    page_number: int
    level: int = 1

    @property
    def chapter_number(self) -> Optional[str]:
        number, _ = parse_chapter_parts(self.title)
        return number

    @property
    def chapter_title(self) -> str:
        _, name = parse_chapter_parts(self.title)
        return name

    @property
    def chapter_number_display(self) -> str:
        return self.chapter_number if self.chapter_number is not None else "-"

    @property
    def insert_location_label(self) -> str:
        if self.chapter_number is not None:
            title = self.chapter_title
            if title:
                return f"{self.chapter_number} {title}"
            return self.chapter_number
        return self.chapter_title or self.title


@dataclass
class TocPageNumberAnchor:
    """Position of a page number on an original TOC page."""

    entry_index: int
    original_page_number: int
    x0: float
    x1: float
    top: float
    bottom: float
    font_size: float
    font_name: str = "Helvetica"
    # 0 = first TOC page, 1 = second, ...
    toc_page_offset: int = 0
    # Draw "- 12 -" instead of "12" when the original used that style.
    wrapped_hyphens: bool = False


@dataclass
class TocStyle:
    """Visual style captured from the original TOC page."""

    title_text: str = "Table of Contents"
    title_font_size: float = 16.0
    entry_font_size: float = 11.0
    left_margin: float = 72.0
    right_margin: float = 72.0
    top_margin: float = 72.0
    line_height: float = 16.0
    level_indent: float = 18.0
    leader_char: str = "."
    font_name: str = "Helvetica"


@dataclass
class TocInfo:
    """Extracted table of contents and layout metadata."""

    entries: List[TocEntry] = field(default_factory=list)
    toc_page_index: int = -1
    toc_page_count: int = 1
    page_width: float = 595.27
    page_height: float = 841.89
    style: TocStyle = field(default_factory=TocStyle)
    source: str = ""
    anchors: List[TocPageNumberAnchor] = field(default_factory=list)

    @property
    def has_toc_page(self) -> bool:
        return self.toc_page_index >= 0 and bool(self.entries)

    @property
    def can_update_in_place(self) -> bool:
        return self.has_toc_page and bool(self.anchors)

    def to_dict(self) -> dict:
        return {
            "entries": [
                {
                    "title": entry.title,
                    "page_number": entry.page_number,
                    "level": entry.level,
                }
                for entry in self.entries
            ],
            "toc_page_index": self.toc_page_index,
            "toc_page_count": self.toc_page_count,
            "page_width": self.page_width,
            "page_height": self.page_height,
            "style": {
                "title_text": self.style.title_text,
                "title_font_size": self.style.title_font_size,
                "entry_font_size": self.style.entry_font_size,
                "left_margin": self.style.left_margin,
                "right_margin": self.style.right_margin,
                "top_margin": self.style.top_margin,
                "line_height": self.style.line_height,
                "level_indent": self.style.level_indent,
                "leader_char": self.style.leader_char,
                "font_name": self.style.font_name,
            },
            "source": self.source,
            "anchors": [
                {
                    "entry_index": anchor.entry_index,
                    "original_page_number": anchor.original_page_number,
                    "x0": anchor.x0,
                    "x1": anchor.x1,
                    "top": anchor.top,
                    "bottom": anchor.bottom,
                    "font_size": anchor.font_size,
                    "font_name": anchor.font_name,
                    "toc_page_offset": anchor.toc_page_offset,
                    "wrapped_hyphens": anchor.wrapped_hyphens,
                }
                for anchor in self.anchors
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TocInfo":
        style_data = data.get("style", {})
        style = TocStyle(
            title_text=str(style_data.get("title_text", "Table of Contents")),
            title_font_size=float(style_data.get("title_font_size", 16.0)),
            entry_font_size=float(style_data.get("entry_font_size", 11.0)),
            left_margin=float(style_data.get("left_margin", 72.0)),
            right_margin=float(style_data.get("right_margin", 72.0)),
            top_margin=float(style_data.get("top_margin", 72.0)),
            line_height=float(style_data.get("line_height", 16.0)),
            level_indent=float(style_data.get("level_indent", 18.0)),
            leader_char=str(style_data.get("leader_char", ".")),
            font_name=str(style_data.get("font_name", "Helvetica")),
        )
        entries = [
            TocEntry(
                title=str(item["title"]),
                page_number=int(item["page_number"]),
                level=int(item.get("level", 1)),
            )
            for item in data.get("entries", [])
        ]
        anchors = [
            TocPageNumberAnchor(
                entry_index=int(item["entry_index"]),
                original_page_number=int(item["original_page_number"]),
                x0=float(item["x0"]),
                x1=float(item["x1"]),
                top=float(item["top"]),
                bottom=float(item["bottom"]),
                font_size=float(item["font_size"]),
                font_name=str(item.get("font_name", "Helvetica")),
                toc_page_offset=int(item.get("toc_page_offset", 0)),
                wrapped_hyphens=bool(item.get("wrapped_hyphens", False)),
            )
            for item in data.get("anchors", [])
        ]
        return cls(
            entries=entries,
            toc_page_index=int(data.get("toc_page_index", -1)),
            toc_page_count=int(data.get("toc_page_count", 1)),
            page_width=float(data.get("page_width", 595.27)),
            page_height=float(data.get("page_height", 841.89)),
            style=style,
            source=str(data.get("source", "")),
            anchors=anchors,
        )


def compute_page_offset(
    original_page: int,
    insertion_counts: List[Tuple[int, int]],
) -> int:
    """
    Return how many pages were inserted before original_page in the main document.

    Args:
        original_page: 1-based page number in the original main PDF
        insertion_counts: (after_page, inserted_page_count) pairs
    """
    offset = 0
    for after_page, count in insertion_counts:
        if after_page < original_page:
            offset += count
    return offset


def adjust_toc_page_numbers(
    entries: List[TocEntry],
    insertion_counts: List[Tuple[int, int]],
) -> List[TocEntry]:
    """Return TOC entries with page numbers shifted for insertions."""
    adjusted: List[TocEntry] = []
    for entry in entries:
        offset = compute_page_offset(entry.page_number, insertion_counts)
        adjusted.append(
            TocEntry(
                title=entry.title,
                page_number=entry.page_number + offset,
                level=entry.level,
            )
        )
    return adjusted


def _page_size(reader: PdfReader, page_index: int) -> Tuple[float, float]:
    page = reader.pages[page_index]
    box = page.mediabox
    return float(box.width), float(box.height)


def _reportlab_font_name(font_name: str) -> str:
    if not font_name:
        return "Helvetica"
    normalized = font_name.split("+")[-1]
    if "bold" in normalized.lower():
        return "Helvetica-Bold"
    if normalized.lower() in {"timesnewroman", "times", "timesnewromanpsmt"}:
        return "Times-Roman"
    if normalized.lower() in {"arial", "calibri", "cambria", "segoeui"}:
        return "Helvetica"
    return "Helvetica"


def _group_words_into_lines(words: List[dict], tolerance: float = 3.0) -> List[List[dict]]:
    lines: List[List[dict]] = []
    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        if not lines:
            lines.append([word])
            continue
        current_line = lines[-1]
        if abs(word["top"] - current_line[0]["top"]) <= tolerance:
            current_line.append(word)
        else:
            lines.append([word])
    return lines


def _extract_page_number_anchors(
    pdf_path: str,
    toc_page_index: int,
    entries: List[TocEntry],
    toc_page_count: int = 1,
) -> List[TocPageNumberAnchor]:
    """
    Locate page-number text positions on every TOC page.

    Anchors are assigned in visual reading order across all TOC pages so the
    second Inhalt page is updated as well as the first.
    """
    anchors: List[TocPageNumberAnchor] = []
    if toc_page_index < 0:
        return anchors

    with pdfplumber.open(pdf_path) as pdf:
        page_count = max(1, toc_page_count)
        for offset in range(page_count):
            page_index = toc_page_index + offset
            if page_index >= len(pdf.pages):
                break
            page = pdf.pages[page_index]
            page_width = float(page.width or 0.0)
            words = page.extract_words(extra_attrs=["size", "fontname"])
            lines = _group_words_into_lines(words)

            for line_words in lines:
                line_words.sort(key=lambda item: item["x0"])
                number_word = None
                for word in reversed(line_words):
                    parsed = _parse_page_number_token(word["text"])
                    if parsed is None:
                        continue
                    if page_width and float(word["x0"]) < page_width * 0.55:
                        continue
                    number_word = word
                    break
                if number_word is None:
                    continue

                original_page_number = _parse_page_number_token(number_word["text"])
                if original_page_number is None:
                    continue

                # Detect "- 46 -" style wrappers around the digit.
                wrapped = False
                x0 = float(number_word["x0"])
                x1 = float(number_word["x1"])
                for word in line_words:
                    if not HYPHEN_ONLY_RE.fullmatch(word["text"].strip()):
                        continue
                    wx0 = float(word["x0"])
                    wx1 = float(word["x1"])
                    if wx1 <= x0 and x0 - wx1 < 40:
                        wrapped = True
                        x0 = min(x0, wx0)
                    if wx0 >= x1 and wx0 - x1 < 40:
                        wrapped = True
                        x1 = max(x1, wx1)

                entry_index = len(anchors)
                if entry_index >= len(entries):
                    # Fall back to matching an unused entry with the same printed number.
                    entry_index = next(
                        (
                            index
                            for index, entry in enumerate(entries)
                            if entry.page_number == original_page_number
                            and index not in {anchor.entry_index for anchor in anchors}
                        ),
                        len(anchors),
                    )

                anchors.append(
                    TocPageNumberAnchor(
                        entry_index=entry_index,
                        original_page_number=original_page_number,
                        x0=x0,
                        x1=x1,
                        top=float(number_word["top"]),
                        bottom=float(number_word["bottom"]),
                        font_size=float(
                            number_word.get("size") or number_word.get("height") or 11.0
                        ),
                        font_name=_reportlab_font_name(
                            str(number_word.get("fontname", ""))
                        ),
                        toc_page_offset=offset,
                        wrapped_hyphens=wrapped,
                    )
                )

    anchors.sort(key=lambda anchor: (anchor.toc_page_offset, anchor.top))
    # Re-assign entry indices strictly in reading order across all TOC pages.
    for order, anchor in enumerate(anchors):
        if order < len(entries):
            anchor.entry_index = order
    return anchors


def _normalize_title(title: str) -> str:
    """Normalize a heading/TOC title for fuzzy matching."""
    text = re.sub(r"\s+", " ", (title or "").strip().lower())
    text = re.sub(r"^[\d.]+\s*", "", text)
    return text


def _dest_to_page_number(reader: PdfReader, dest) -> int:
    """Resolve a PDF destination (named or explicit) to a 1-based page number."""
    if dest is None:
        return 0

    try:
        if hasattr(dest, "get_object"):
            dest = dest.get_object()
    except Exception:
        pass

    # Named destination (Name or string)
    try:
        from PyPDF2.generic import NameObject, TextStringObject

        named = getattr(reader, "named_destinations", None) or {}
        name: Optional[str] = None
        if isinstance(dest, NameObject):
            name = str(dest)
            if name.startswith("/"):
                name = name[1:]
        elif isinstance(dest, (str, TextStringObject, bytes)):
            name = dest.decode("utf-8", errors="ignore") if isinstance(dest, bytes) else str(dest)

        if name:
            target = named.get(name) or named.get("/" + name)
            if target is not None:
                try:
                    return int(reader.get_destination_page_number(target)) + 1
                except Exception:
                    dest = target
    except Exception:
        pass

    try:
        return int(reader.get_destination_page_number(dest)) + 1
    except Exception:
        pass

    # Explicit [page_ref, /XYZ|/Fit, ...]
    try:
        from PyPDF2.generic import ArrayObject

        if isinstance(dest, (list, ArrayObject)) and len(dest) > 0:
            page_ref = dest[0]
            try:
                if hasattr(page_ref, "get_object"):
                    page_ref = page_ref.get_object()
            except Exception:
                pass
            for index, page in enumerate(reader.pages):
                try:
                    if page.indirect_reference == dest[0]:
                        return index + 1
                except Exception:
                    pass
                try:
                    if page == page_ref:
                        return index + 1
                except Exception:
                    pass
    except Exception:
        pass

    return 0


def _extract_toc_link_targets(
    reader: PdfReader,
    toc_page_index: int,
    toc_page_count: int = 1,
) -> List[int]:
    """
    Return destination page numbers for TOC hyperlink annotations, top-to-bottom.

    Word-exported TOCs almost always wrap each entry in a GoTo link that points at
    the real chapter page — unlike extracted text, which can pick up the TOC sheet
    page number instead.
    """
    if toc_page_index < 0:
        return []

    targets: List[Tuple[float, float, float, int]] = []  # page_index, -y_top, x0, dest_page
    last_index = min(len(reader.pages), toc_page_index + max(1, toc_page_count))

    for page_index in range(toc_page_index, last_index):
        page = reader.pages[page_index]
        annots = page.get("/Annots")
        if not annots:
            continue
        try:
            annot_list = list(annots)
        except Exception:
            continue

        for annot_ref in annot_list:
            try:
                annot = annot_ref.get_object() if hasattr(annot_ref, "get_object") else annot_ref
            except Exception:
                continue
            subtype = str(annot.get("/Subtype", ""))
            if subtype not in {"/Link", "Link"}:
                continue

            dest = annot.get("/Dest")
            action = annot.get("/A")
            if action is not None:
                try:
                    action = action.get_object() if hasattr(action, "get_object") else action
                except Exception:
                    pass
                try:
                    if str(action.get("/S", "")) in {"/GoTo", "GoTo"}:
                        dest = action.get("/D")
                except Exception:
                    pass

            dest_page = _dest_to_page_number(reader, dest)
            if dest_page <= 0:
                continue

            rect = annot.get("/Rect")
            try:
                y_top = float(rect[3])
                x0 = float(rect[0])
            except Exception:
                y_top, x0 = 0.0, 0.0

            targets.append((float(page_index), -y_top, x0, dest_page))

    targets.sort()
    # Collapse overlapping/stacked link rects on the same row.
    pages: List[int] = []
    last_key: Optional[Tuple[int, int]] = None
    for page_index, neg_y, _x0, dest_page in targets:
        key = (int(page_index), int(neg_y) // 3)
        if key == last_key:
            continue
        last_key = key
        pages.append(dest_page)
    return pages


def _toc_sheet_pages(toc_page_index: int, toc_page_count: int) -> set:
    """1-based page numbers that belong to the TOC sheet(s), plus a small margin."""
    if toc_page_index < 0:
        return set()
    start = toc_page_index + 1
    # Include one extra page so a 2-page TOC still counts when count was underestimated.
    end = toc_page_index + max(1, toc_page_count) + 1
    return set(range(start, end + 1))


def _entries_point_at_toc_pages(
    entries: List[TocEntry],
    toc_page_index: int,
    toc_page_count: int,
) -> bool:
    """True when reported page numbers all fall on the TOC sheet(s)."""
    if not entries or toc_page_index < 0:
        return False
    toc_pages = _toc_sheet_pages(toc_page_index, toc_page_count)
    entry_pages = {entry.page_number for entry in entries if entry.page_number > 0}
    if not entry_pages:
        return True
    return entry_pages <= toc_pages


def _match_outline_page(title: str, outline_entries: List[TocEntry]) -> int:
    needle = _normalize_title(title)
    if not needle:
        return 0
    for entry in outline_entries:
        other = _normalize_title(entry.title)
        if not other:
            continue
        if needle == other or needle in other or other in needle:
            return entry.page_number
    return 0


def _printed_numbers_are_trustworthy(
    entries: List[TocEntry],
    toc_page_index: int,
    toc_page_count: int,
) -> bool:
    """True when printed TOC numbers look like real chapter pages, not the TOC sheet."""
    if len(entries) < 2:
        return False
    if _entries_point_at_toc_pages(entries, toc_page_index, toc_page_count):
        return False
    pages = [entry.page_number for entry in entries if entry.page_number > 0]
    if len(pages) < 2:
        return False
    # A real TOC spans multiple destination pages.
    return len(set(pages)) >= 2 and max(pages) > min(pages)


def _correct_entry_page_numbers(
    entries: List[TocEntry],
    outline_entries: List[TocEntry],
    link_targets: List[int],
    toc_page_index: int,
    toc_page_count: int,
) -> List[TocEntry]:
    """
    Keep printed TOC page numbers when they match the document TOC.

    Only fall back to hyperlink/outline destinations when the printed numbers
    clearly point at the TOC sheet itself (the earlier bug).
    """
    if _printed_numbers_are_trustworthy(entries, toc_page_index, toc_page_count):
        return [
            TocEntry(title=e.title, page_number=e.page_number, level=e.level)
            for e in entries
        ]

    toc_pages = _toc_sheet_pages(toc_page_index, toc_page_count)
    corrected: List[TocEntry] = []

    for index, entry in enumerate(entries):
        page_number = entry.page_number
        on_toc = page_number in toc_pages or page_number <= 0
        link_page = link_targets[index] if index < len(link_targets) else 0
        outline_page = _match_outline_page(entry.title, outline_entries)

        if on_toc and link_page > 0:
            page_number = link_page
        elif on_toc and outline_page > 0:
            page_number = outline_page
        elif page_number <= 0 and link_page > 0:
            page_number = link_page
        elif page_number <= 0 and outline_page > 0:
            page_number = outline_page

        corrected.append(
            TocEntry(title=entry.title, page_number=page_number, level=entry.level)
        )

    if outline_entries and _entries_point_at_toc_pages(
        corrected, toc_page_index, toc_page_count
    ):
        return [
            TocEntry(title=e.title, page_number=e.page_number, level=e.level)
            for e in outline_entries
        ]

    return corrected


def _line_has_right_page_number(
    line_words: List[dict],
    page_width: float,
) -> Tuple[Optional[dict], Optional[int]]:
    """Return (number_word, page_number) if a right-column TOC page number exists."""
    for word in reversed(line_words):
        parsed = _parse_page_number_token(word["text"])
        if parsed is None:
            continue
        if page_width and float(word["x0"]) < page_width * 0.55:
            continue
        return word, parsed
    return None, None


def _title_from_toc_line_words(
    line_words: List[dict],
    number_word: Optional[dict] = None,
    page_number: Optional[int] = None,
) -> str:
    """Build the title text from a TOC line, stripping leaders and page-number wrappers."""
    title_words = []
    for word in line_words:
        if number_word is not None and word is number_word:
            continue
        if number_word is not None and HYPHEN_ONLY_RE.fullmatch(word["text"].strip()):
            if float(word["x0"]) >= float(number_word["x0"]) - 30:
                continue
        if (
            page_number is not None
            and _parse_page_number_token(word["text"]) == page_number
            and number_word is not None
            and float(word["x0"]) >= float(number_word["x0"]) - 5
        ):
            continue
        if re.fullmatch(r"[.\u00b7·\-_]{2,}", word["text"]):
            continue
        title_words.append(word)

    title = " ".join(word["text"] for word in title_words).strip()
    title = re.sub(r"\s+", " ", title)
    title = re.sub(r"[\.\u00b7·]{2,}\s*$", "", title).strip()
    return title


def _looks_like_toc_entry_start(title: str) -> bool:
    """True when a line without a page number still looks like a TOC entry / wrap start."""
    stripped = (title or "").strip()
    if len(stripped) < 2:
        return False
    if re.fullmatch(r"[.\u00b7·\-_\s]+", stripped):
        return False
    return True


def _merge_wrapped_toc_title(pending: str, continuation: str) -> str:
    pending = (pending or "").strip()
    continuation = (continuation or "").strip()
    if not pending:
        return continuation
    if not continuation:
        return pending
    # Avoid duplicating if the continuation already starts with the pending text.
    if continuation.lower().startswith(pending.lower()):
        return continuation
    return f"{pending} {continuation}".strip()


def _extract_toc_entries_from_layout(
    pdf_path: str,
    toc_page_index: int,
    toc_page_count: int = 1,
) -> List[TocEntry]:
    """
    Build TOC entries from word positions: title on the left, page number on the right.

    Handles hyphen-wrapped page numbers such as "- 12 -" and multi-line titles where
    the page number appears only on the last wrapped line.
    """
    if toc_page_index < 0:
        return []

    entries: List[TocEntry] = []
    pending_title = ""
    pending_level = 1

    try:
        with pdfplumber.open(pdf_path) as pdf:
            last_index = min(len(pdf.pages), toc_page_index + max(1, toc_page_count))
            for page_index in range(toc_page_index, last_index):
                page = pdf.pages[page_index]
                words = page.extract_words(extra_attrs=["size", "fontname"])
                page_width = float(page.width or 0.0)

                for line_words in _group_words_into_lines(words):
                    line_words = sorted(line_words, key=lambda item: item["x0"])
                    if not line_words:
                        continue

                    line_text = " ".join(word["text"] for word in line_words).strip()
                    if _is_toc_title_text(line_text):
                        pending_title = ""
                        continue

                    number_word, page_number = _line_has_right_page_number(
                        line_words, page_width
                    )
                    title = _title_from_toc_line_words(
                        line_words, number_word, page_number
                    )

                    left = float(line_words[0]["x0"])
                    level = 1
                    if left > 90:
                        level = 2
                    if left > 120:
                        level = 3

                    if page_number is None:
                        # Title wrap / first line of a multi-line TOC entry.
                        if _looks_like_toc_entry_start(title):
                            # New numbered heading replaces any unfinished wrap.
                            if CHAPTER_NUMBER_RE.match(title.strip()):
                                pending_title = title
                                pending_level = level
                            elif pending_title:
                                pending_title = _merge_wrapped_toc_title(
                                    pending_title, title
                                )
                            else:
                                pending_title = title
                                pending_level = level
                        continue

                    entry_level = pending_level if pending_title else level
                    full_title = _merge_wrapped_toc_title(pending_title, title)
                    pending_title = ""
                    pending_level = 1
                    if len(full_title) < 2:
                        continue

                    entries.append(
                        TocEntry(
                            title=full_title,
                            page_number=page_number,
                            level=entry_level,
                        )
                    )
    except Exception:
        return entries

    return entries


def _walk_outline(
    reader: PdfReader,
    outline,
    level: int,
    entries: List[TocEntry],
) -> None:
    for item in outline:
        if isinstance(item, list):
            _walk_outline(reader, item, level + 1, entries)
            continue
        title = getattr(item, "title", None) or str(item)
        title = str(title).strip()
        if not title:
            continue
        try:
            page_number = reader.get_destination_page_number(item) + 1
        except Exception:
            page_number = 0
        if page_number > 0:
            entries.append(TocEntry(title=title, page_number=page_number, level=level))


def _extract_outline_entries(reader: PdfReader) -> List[TocEntry]:
    outline = getattr(reader, "outline", None)
    if not outline:
        return []
    entries: List[TocEntry] = []
    _walk_outline(reader, outline, 1, entries)
    return entries


def _parse_toc_lines(text: str) -> Tuple[Optional[str], List[TocEntry]]:
    lines = [line.rstrip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return None, []

    title_text = _is_toc_title_text(text)
    entries: List[TocEntry] = []
    pending_title = ""
    pending_indent = ""

    for line in lines:
        if title_text and line.strip() == title_text:
            continue
        if _is_toc_title_text(line):
            continue

        match = TOC_ENTRY_RE.match(line)
        if match:
            indent, title, page_str = match.groups()
            full_title = _merge_wrapped_toc_title(pending_title, title.strip())
            pending_title = ""
            pending_indent = ""
            level = max(1, min(6, len(indent) // 2 + 1))
            entries.append(
                TocEntry(
                    title=full_title,
                    page_number=int(page_str),
                    level=level,
                )
            )
            continue

        # Multi-line TOC title: keep lines without a trailing page number.
        stripped = line.strip()
        if not _looks_like_toc_entry_start(stripped):
            continue
        # Ignore bare leader lines.
        if re.fullmatch(r"[.\u00b7·\-_\s]+", stripped):
            continue
        indent_match = re.match(r"^(\s*)", line)
        indent = indent_match.group(1) if indent_match else ""
        if CHAPTER_NUMBER_RE.match(stripped):
            pending_title = stripped
            pending_indent = indent
        elif pending_title:
            pending_title = _merge_wrapped_toc_title(pending_title, stripped)
        else:
            pending_title = stripped
            pending_indent = indent

    return title_text, entries


def _extract_toc_page_entries(
    reader: PdfReader,
) -> Tuple[int, List[TocEntry], TocStyle, int]:
    """
    Find TOC page(s) near the front of the document.

    Returns:
        (first_toc_page_index, entries, style, toc_page_count)
    """
    best_index = -1
    best_entries: List[TocEntry] = []
    best_title: Optional[str] = None
    toc_page_count = 0

    scan_limit = min(20, len(reader.pages))
    for page_index in range(scan_limit):
        text = reader.pages[page_index].extract_text() or ""
        title_text, entries = _parse_toc_lines(text)
        titled = title_text is not None

        if len(entries) < 2 and not titled:
            if best_index >= 0:
                break
            continue

        if best_index < 0:
            best_index = page_index
            best_entries = list(entries)
            best_title = title_text
            toc_page_count = 1
        else:
            best_entries.extend(entries)
            toc_page_count += 1

        # After a titled TOC page, keep reading consecutive TOC-like pages.
        if titled or len(entries) >= 5:
            next_index = page_index + 1
            while next_index < scan_limit and toc_page_count < 8:
                next_text = reader.pages[next_index].extract_text() or ""
                next_title, next_entries = _parse_toc_lines(next_text)
                if next_title is not None:
                    break
                if len(next_entries) >= 2:
                    best_entries.extend(next_entries)
                    toc_page_count += 1
                    next_index += 1
                    continue
                # Continuation may only be recoverable via layout.
                if titled and toc_page_count < 3:
                    toc_page_count += 1
                break
            break

    if best_index < 0:
        return -1, [], TocStyle(), 0

    style = TocStyle(title_text=best_title or "Table of Contents")
    return best_index, best_entries, style, max(1, toc_page_count)


def _extract_docx_headings(docx_path: str) -> List[TocEntry]:
    document = docx.Document(docx_path)
    entries: List[TocEntry] = []
    for paragraph in document.paragraphs:
        style_name = paragraph.style.name if paragraph.style is not None else ""
        if not style_name.startswith("Heading"):
            continue
        title = paragraph.text.strip()
        if not title:
            continue
        try:
            level = int(style_name.split()[-1])
        except (ValueError, IndexError):
            level = 1
        entries.append(TocEntry(title=title, page_number=0, level=level))
    return entries


def _resolve_heading_pages(
    reader: PdfReader,
    entries: List[TocEntry],
    skip_page_indices: Optional[set] = None,
) -> List[TocEntry]:
    """
    Locate heading titles in the PDF, skipping TOC pages so titles are not
    matched against their appearance in the table of contents.
    """
    skip_page_indices = skip_page_indices or set()
    resolved: List[TocEntry] = []
    search_from = 0
    for entry in entries:
        page_number = 0
        needle = entry.title[:80].strip()
        if not needle:
            continue
        for page_index in range(search_from, len(reader.pages)):
            if page_index in skip_page_indices:
                continue
            text = (reader.pages[page_index].extract_text() or "").lower()
            if needle.lower() in text:
                page_number = page_index + 1
                search_from = page_index
                break
        if page_number > 0:
            resolved.append(
                TocEntry(
                    title=entry.title,
                    page_number=page_number,
                    level=entry.level,
                )
            )
    return resolved


def extract_toc_from_pdf(pdf_path: str) -> TocInfo:
    """Extract TOC entries and TOC page metadata from a PDF."""
    reader = PdfReader(pdf_path, strict=False)
    if not reader.pages:
        return TocInfo()

    width, height = _page_size(reader, 0)
    outline_entries = _extract_outline_entries(reader)
    toc_page_index, page_entries, style, toc_page_count = _extract_toc_page_entries(reader)

    # Prefer layout extraction (handles " - 12 - " page numbers and leaders).
    layout_entries = _extract_toc_entries_from_layout(
        pdf_path, toc_page_index, max(1, toc_page_count or 1)
    )
    if layout_entries:
        # If layout found a richer / better TOC, use it.
        if len(layout_entries) >= len(page_entries):
            page_entries = layout_entries
        elif _printed_numbers_are_trustworthy(
            layout_entries, toc_page_index, toc_page_count or 1
        ) and not _printed_numbers_are_trustworthy(
            page_entries, toc_page_index, toc_page_count or 1
        ):
            page_entries = layout_entries

    link_targets = _extract_toc_link_targets(
        reader, toc_page_index, max(1, toc_page_count or 1)
    )

    entries: List[TocEntry]
    source = ""
    if page_entries:
        entries = _correct_entry_page_numbers(
            page_entries,
            outline_entries,
            link_targets,
            toc_page_index,
            toc_page_count or 1,
        )
        source = "toc_page"
        # Do NOT replace a trustworthy printed TOC with outline/PDF indices.
        if (
            not _printed_numbers_are_trustworthy(
                entries, toc_page_index, toc_page_count or 1
            )
            and outline_entries
            and _entries_point_at_toc_pages(entries, toc_page_index, toc_page_count or 1)
        ):
            entries = outline_entries
            source = "outline"
    elif outline_entries:
        entries = outline_entries
        source = "outline"
    else:
        return TocInfo(page_width=width, page_height=height)

    if toc_page_index >= 0:
        page_width, page_height = _page_size(reader, toc_page_index)
    else:
        page_width, page_height = width, height

    anchors: List[TocPageNumberAnchor] = []
    if toc_page_index >= 0 and source == "toc_page":
        anchor_entries = page_entries or entries
        anchors = _extract_page_number_anchors(
            pdf_path,
            toc_page_index,
            anchor_entries,
            toc_page_count=max(1, toc_page_count or 1),
        )

    return TocInfo(
        entries=entries,
        toc_page_index=toc_page_index,
        toc_page_count=max(1, toc_page_count or 1),
        page_width=page_width,
        page_height=page_height,
        style=style,
        source=source,
        anchors=anchors,
    )


def extract_toc(
    file_path: str,
    resolve_pdf: Callable[[str], str],
) -> TocInfo:
    """
    Extract TOC from a PDF or Word document.

    Args:
        file_path: Path to the main document
        resolve_pdf: Callable that returns a PDF path for the given file
    """
    pdf_path = resolve_pdf(file_path)
    info = extract_toc_from_pdf(pdf_path)

    suffix = Path(file_path).suffix.lower()
    if not info.entries and suffix == ".docx":
        try:
            heading_entries = _extract_docx_headings(file_path)
        except Exception:
            heading_entries = []
        if heading_entries:
            reader = PdfReader(pdf_path, strict=False)
            skip = set()
            if info.toc_page_index >= 0:
                skip = {
                    info.toc_page_index + offset
                    for offset in range(0, 3)
                    if info.toc_page_index + offset < len(reader.pages)
                }
            # Also skip early pages that look like TOC when index unknown.
            if not skip:
                skip = {0, 1, 2, 3, 4}
            resolved = _resolve_heading_pages(reader, heading_entries, skip)
            if resolved:
                info.entries = resolved
                info.source = "docx_headings"

    return info


def build_toc_overlay_pdf(
    toc_info: TocInfo,
    adjusted_entries: List[TocEntry],
    output_path: str,
) -> bool:
    """
    Build a multi-page transparent overlay — one page per TOC sheet — that covers
    old page numbers and draws the adjusted ones.
    """
    if not toc_info.anchors:
        return False

    width = toc_info.page_width
    height = toc_info.page_height
    page_count = max(
        1,
        toc_info.toc_page_count,
        max((anchor.toc_page_offset for anchor in toc_info.anchors), default=0) + 1,
    )
    c = canvas.Canvas(output_path, pagesize=(width, height))

    for offset in range(page_count):
        page_anchors = [
            anchor for anchor in toc_info.anchors if anchor.toc_page_offset == offset
        ]
        for anchor in page_anchors:
            if anchor.entry_index >= len(adjusted_entries):
                continue
            new_page_number = adjusted_entries[anchor.entry_index].page_number
            if anchor.wrapped_hyphens:
                new_label = f"- {new_page_number} -"
            else:
                new_label = str(new_page_number)
            font_name = anchor.font_name
            font_size = anchor.font_size

            c.setFont(font_name, font_size)
            text_width = c.stringWidth(new_label, font_name, font_size)
            draw_x = anchor.x1 - text_width
            baseline_y = height - anchor.bottom

            pad_x = max(8.0, font_size * 0.6)
            pad_y = max(3.0, font_size * 0.25)
            white_left = min(anchor.x0, draw_x) - pad_x
            white_right = max(anchor.x1, draw_x + text_width) + pad_x
            white_bottom = baseline_y - pad_y
            white_top = baseline_y + font_size + pad_y

            c.setFillColor(white)
            c.rect(
                white_left,
                white_bottom,
                white_right - white_left,
                white_top - white_bottom,
                fill=1,
                stroke=0,
            )
            c.setFillColor("black")
            c.drawString(draw_x, baseline_y, new_label)

        c.showPage()

    c.save()
    return True


def update_toc_page_in_place(
    pdf_path: str,
    toc_info: TocInfo,
    insertion_counts: List[Tuple[int, int]],
    output_path: str,
    temp_dir: str,
    merge_stamp_on_top=None,
) -> bool:
    """
    Update page numbers on every TOC page while preserving links and other content.

    Returns:
        True when at least one TOC page was updated.
    """
    if not toc_info.can_update_in_place:
        return False

    import os

    try:
        import pikepdf
    except ImportError as exc:
        raise ImportError(
            "pikepdf is required to update TOC page numbers while preserving links. "
            "Install it with: pip install pikepdf"
        ) from exc

    adjusted_entries = adjust_toc_page_numbers(toc_info.entries, insertion_counts)
    original_toc_page = toc_info.toc_page_index + 1
    new_toc_index = (
        compute_page_offset(original_toc_page, insertion_counts) + toc_info.toc_page_index
    )
    toc_page_count = max(
        1,
        toc_info.toc_page_count,
        max((anchor.toc_page_offset for anchor in toc_info.anchors), default=0) + 1,
    )

    overlay_path = os.path.join(temp_dir, "toc_overlay.pdf")
    if not build_toc_overlay_pdf(toc_info, adjusted_entries, overlay_path):
        return False

    with pikepdf.Pdf.open(pdf_path) as pdf:
        with pikepdf.Pdf.open(overlay_path) as overlay_pdf:
            for offset in range(min(toc_page_count, len(overlay_pdf.pages))):
                target_index = new_toc_index + offset
                if target_index < 0 or target_index >= len(pdf.pages):
                    continue
                pdf.pages[target_index].add_overlay(
                    overlay_pdf.pages[offset],
                    shrink=False,
                    expand=False,
                )
        pdf.save(output_path)

    if os.path.exists(overlay_path):
        os.remove(overlay_path)
    return True
