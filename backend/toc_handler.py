"""
Table of contents extraction and in-place update for The Reportinator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

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
# Bare numbered heading such as "1.2.3" or "1." with little/no title text.
CHAPTER_NUMBER_ONLY_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s*$")


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
    only = CHAPTER_NUMBER_ONLY_RE.match(stripped)
    if only:
        return only.group(1), ""
    return None, stripped


def level_from_chapter_number(title: str) -> Optional[int]:
    """
    Infer hierarchy depth from a dotted chapter number.

    Examples:
        "1 Introduction" -> 1
        "1.2 Scope" -> 2
        "1.2.3 Details" -> 3
        "Appendix" -> None
    """
    number, _ = parse_chapter_parts(title)
    if not number:
        return None
    return number.count(".") + 1


def _levels_from_relative_indents(
    lefts: List[float],
    tolerance: float = 10.0,
) -> List[int]:
    """
    Map absolute left positions to 1-based levels by clustering similar indents.
    """
    if not lefts:
        return []
    ordered = sorted(set(round(x, 1) for x in lefts))
    clusters: List[float] = [ordered[0]]
    for value in ordered[1:]:
        if value - clusters[-1] >= tolerance:
            clusters.append(value)
    levels: List[int] = []
    for left in lefts:
        best = min(range(len(clusters)), key=lambda i: abs(clusters[i] - left))
        levels.append(best + 1)
    return levels


def apply_hierarchy_levels(entries: List[TocEntry]) -> List[TocEntry]:
    """
    Ensure TOC entries have nesting levels suitable for PDF bookmarks.

    Prefers dotted chapter numbers (1 / 1.2 / 1.2.3). Falls back to any
    existing level, then smooths so depth never jumps by more than one.
    """
    if not entries:
        return []

    numbered = [level_from_chapter_number(entry.title) for entry in entries]
    numbered_count = sum(1 for level in numbered if level is not None)

    resolved: List[int] = []
    if numbered_count >= max(2, (len(entries) + 1) // 2):
        # Numbered TOC: use chapter depth; fill gaps from neighbours / existing.
        last_level = 1
        for entry, num_level in zip(entries, numbered):
            if num_level is not None:
                level = num_level
            else:
                level = max(1, int(entry.level or last_level))
            resolved.append(level)
            last_level = level
    else:
        resolved = [max(1, int(entry.level or 1)) for entry in entries]

    # Smooth: depth may only increase by one step at a time.
    smoothed: List[int] = []
    for index, level in enumerate(resolved):
        level = max(1, int(level))
        if index == 0:
            if numbered[0] is None:
                level = 1
        else:
            level = min(level, smoothed[-1] + 1)
        smoothed.append(level)

    return [
        TocEntry(
            title=entry.title,
            page_number=entry.page_number,
            level=level,
            page_label=entry.page_label,
        )
        for entry, level in zip(entries, smoothed)
    ]


def _entries_have_outline_hierarchy(entries: List[TocEntry]) -> bool:
    """True when entries already carry multi-level nesting (e.g. from PDF outline)."""
    if len(entries) < 2:
        return False
    levels = [max(1, int(entry.level or 1)) for entry in entries]
    return max(levels) > 1


@dataclass
class TocEntry:
    """A single table-of-contents entry."""

    title: str
    page_number: int
    level: int = 1
    # Exact page label as printed in the TOC (e.g. "13" or "10A.9.14").
    page_label: str = ""

    @property
    def display_page_label(self) -> str:
        label = (self.page_label or "").strip()
        return label if label else str(self.page_number)

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
class TocGoToLink:
    """A GoTo hyperlink on a TOC page, with geometry and destination."""

    page_index: int  # 0-based PDF page index
    toc_page_offset: int  # 0 = first TOC page
    rect: Tuple[float, float, float, float]  # PDF coords: x0, y0, x1, y1
    dest_page: int  # 1-based destination page


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
    # Full TOC line bounds (title + leaders + page number) for row highlighting.
    line_x0: float = 0.0
    line_x1: float = 0.0
    line_top: float = 0.0
    line_bottom: float = 0.0


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
                    "page_label": entry.page_label,
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
                    "line_x0": anchor.line_x0,
                    "line_x1": anchor.line_x1,
                    "line_top": anchor.line_top,
                    "line_bottom": anchor.line_bottom,
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
                page_label=str(item.get("page_label", "") or ""),
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
                line_x0=float(item.get("line_x0", 0.0)),
                line_x1=float(item.get("line_x1", 0.0)),
                line_top=float(item.get("line_top", 0.0)),
                line_bottom=float(item.get("line_bottom", 0.0)),
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
                page_label=entry.page_label,
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


def _pdf_rect_to_plumber_band(
    rect: Tuple[float, float, float, float],
    page_height: float,
) -> Tuple[float, float]:
    """Convert a PDF /Rect (bottom-left origin) to pdfplumber top/bottom."""
    pdf_y0 = float(rect[1])
    pdf_y1 = float(rect[3])
    top = page_height - pdf_y1
    bottom = page_height - pdf_y0
    return top, bottom


def _words_in_vertical_band(
    words: List[dict],
    band_top: float,
    band_bottom: float,
    tolerance: float = 4.0,
) -> List[dict]:
    """Return words whose vertical center falls inside a plumber top/bottom band."""
    selected: List[dict] = []
    for word in words:
        center = (float(word.get("top", 0.0)) + float(word.get("bottom", 0.0))) / 2.0
        if band_top - tolerance <= center <= band_bottom + tolerance:
            selected.append(word)
    return selected


def _collapse_toc_goto_links(links: List[TocGoToLink]) -> List[TocGoToLink]:
    """Drop stacked duplicate link rects on the same TOC row."""
    if not links:
        return []
    ordered = sorted(
        links,
        key=lambda link: (link.page_index, -link.rect[3], link.rect[0]),
    )
    collapsed: List[TocGoToLink] = []
    last_key: Optional[Tuple[int, int]] = None
    for link in ordered:
        key = (link.page_index, int(-link.rect[3]) // 3)
        if key == last_key:
            continue
        last_key = key
        collapsed.append(link)
    return collapsed


def _extract_toc_goto_links_detailed(
    reader: PdfReader,
    toc_page_index: int,
    toc_page_count: int = 1,
) -> List[TocGoToLink]:
    """Return GoTo links on TOC pages with rects and destinations, reading order."""
    if toc_page_index < 0:
        return []

    links: List[TocGoToLink] = []
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
                annot = (
                    annot_ref.get_object()
                    if hasattr(annot_ref, "get_object")
                    else annot_ref
                )
            except Exception:
                continue
            subtype = str(annot.get("/Subtype", ""))
            if subtype not in {"/Link", "Link"}:
                continue

            dest = annot.get("/Dest")
            action = annot.get("/A")
            if action is not None:
                try:
                    action = (
                        action.get_object()
                        if hasattr(action, "get_object")
                        else action
                    )
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
                rect_t = (
                    float(rect[0]),
                    float(rect[1]),
                    float(rect[2]),
                    float(rect[3]),
                )
            except Exception:
                continue

            links.append(
                TocGoToLink(
                    page_index=page_index,
                    toc_page_offset=page_index - toc_page_index,
                    rect=rect_t,
                    dest_page=dest_page,
                )
            )

    return _collapse_toc_goto_links(links)


def _match_entry_index_for_link_dest(
    dest_page: int,
    entries: List[TocEntry],
    used_indices: set,
) -> Optional[int]:
    """
    Map a TOC GoTo destination to an outline entry index.

    Entries are matched in document order so duplicate page numbers (e.g. two
    rows both pointing at page 2) stay aligned with link order.
    """
    for index, entry in enumerate(entries):
        if index in used_indices:
            continue
        if entry.page_number == dest_page:
            return index
    return None


def _anchor_from_link_row(
    link: TocGoToLink,
    row_words: List[dict],
    page_width: float,
    entry_index: int,
) -> Optional[TocPageNumberAnchor]:
    """Build a page-number anchor from words on a TOC hyperlink row."""
    if not row_words:
        return None

    row_words = sorted(row_words, key=lambda item: item["x0"])
    label_words, page_label, parsed_nav = _line_has_right_page_number(
        row_words, page_width
    )
    if parsed_nav is None:
        return None

    number_word = None
    right_band = [
        word
        for word in row_words
        if not page_width or float(word["x0"]) >= page_width * 0.55
    ]
    candidates = label_words or right_band or row_words
    for word in candidates:
        token = _parse_page_number_token(word.get("text") or "")
        if token == parsed_nav:
            number_word = word
            break
    if number_word is None:
        for word in candidates:
            if _parse_page_number_token(word.get("text") or "") is not None:
                number_word = word
                break
    if number_word is None:
        return None

    wrapped = bool(page_label and re.match(r"^[\-\u2013]", page_label.strip()))
    if label_words:
        x0 = min(float(w["x0"]) for w in label_words)
        x1 = max(float(w["x1"]) for w in label_words)
        wrapped = wrapped or any(
            HYPHEN_ONLY_RE.fullmatch((w.get("text") or "").strip())
            for w in label_words
        )
    else:
        x0 = float(number_word["x0"])
        x1 = float(number_word["x1"])
        for word in row_words:
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

    line_x0 = min(float(w["x0"]) for w in row_words)
    line_x1 = max(float(w["x1"]) for w in row_words)
    line_top = min(float(w["top"]) for w in row_words)
    line_bottom = max(float(w["bottom"]) for w in row_words)

    return TocPageNumberAnchor(
        entry_index=entry_index,
        original_page_number=parsed_nav,
        x0=x0,
        x1=x1,
        top=float(number_word["top"]),
        bottom=float(number_word["bottom"]),
        font_size=float(
            number_word.get("size") or number_word.get("height") or 11.0
        ),
        font_name=_reportlab_font_name(str(number_word.get("fontname", ""))),
        toc_page_offset=link.toc_page_offset,
        wrapped_hyphens=wrapped,
        line_x0=line_x0,
        line_x1=line_x1,
        line_top=line_top,
        line_bottom=line_bottom,
    )


def _extract_page_number_anchors(
    pdf_path: str,
    toc_page_index: int,
    entries: List[TocEntry],
    toc_page_count: int = 1,
) -> List[TocPageNumberAnchor]:
    """
    Locate page-number text positions on every TOC page.

    Each anchor is tied to a real TOC GoTo hyperlink row (not footer noise) and
    mapped to the outline entry whose destination matches the link target.

    ``line_*`` bounds cover the full chapter block when a title wraps onto
    multiple lines (page number only on the last line).
    """
    anchors: List[TocPageNumberAnchor] = []
    if toc_page_index < 0 or not entries:
        return anchors

    reader = PdfReader(pdf_path, strict=False)
    links = _extract_toc_goto_links_detailed(
        reader, toc_page_index, max(1, toc_page_count)
    )
    if not links:
        return anchors

    used_entry_indices: set = set()
    words_by_page: Dict[int, List[dict]] = {}

    with pdfplumber.open(pdf_path) as pdf:
        for link in links:
            if link.page_index >= len(pdf.pages):
                continue
            page = pdf.pages[link.page_index]
            page_width = float(page.width or 0.0)
            page_height = float(page.height or 0.0)

            if link.page_index not in words_by_page:
                words_by_page[link.page_index] = _dedupe_overlapping_words(
                    page.extract_words(extra_attrs=["size", "fontname"]) or []
                )
            words = words_by_page[link.page_index]

            band_top, band_bottom = _pdf_rect_to_plumber_band(link.rect, page_height)
            row_words = _words_in_vertical_band(words, band_top, band_bottom)
            if not row_words:
                continue

            entry_index = _match_entry_index_for_link_dest(
                link.dest_page, entries, used_entry_indices
            )
            if entry_index is None:
                continue

            anchor = _anchor_from_link_row(
                link, row_words, page_width, entry_index
            )
            if anchor is None:
                continue

            used_entry_indices.add(entry_index)
            anchors.append(anchor)

    anchors.sort(key=lambda anchor: (anchor.toc_page_offset, anchor.top))
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
    links = _extract_toc_goto_links_detailed(
        reader, toc_page_index, max(1, toc_page_count)
    )
    return [link.dest_page for link in links]


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


def _match_outline_entry(title: str, outline_entries: List[TocEntry]) -> Optional[TocEntry]:
    needle = _normalize_title(title)
    if not needle:
        return None
    for entry in outline_entries:
        other = _normalize_title(entry.title)
        if not other:
            continue
        if needle == other or needle in other or other in needle:
            return entry
    return None


def _match_outline_page(title: str, outline_entries: List[TocEntry]) -> int:
    matched = _match_outline_entry(title, outline_entries)
    return matched.page_number if matched is not None else 0


def _enrich_levels_from_outline(
    entries: List[TocEntry],
    outline_entries: List[TocEntry],
) -> List[TocEntry]:
    """Copy nesting depth from PDF outline entries when TOC levels are flat."""
    if not entries or not outline_entries:
        return entries

    outline_levels = {id(e): e.level for e in outline_entries}
    if max(outline_levels.values(), default=1) <= 1:
        return entries

    enriched: List[TocEntry] = []
    for entry in entries:
        level = entry.level
        if level_from_chapter_number(entry.title) is None:
            matched = _match_outline_entry(entry.title, outline_entries)
            if matched is not None and matched.level > 0:
                level = matched.level
        enriched.append(
            TocEntry(
                title=entry.title,
                page_number=entry.page_number,
                level=level,
                page_label=entry.page_label,
            )
        )
    return enriched


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
    Preserve printed TOC page labels; set navigable PDF pages.

    Printed/layout numbers (e.g. ``- 50 -``, or the larger of dual labels) are
    preferred when trustworthy. TOC hyperlinks and outline matches are used
    only as fallbacks when printed numbers are unreliable.
    """
    toc_pages = _toc_sheet_pages(toc_page_index, toc_page_count)
    printed_ok = _printed_numbers_are_trustworthy(
        entries, toc_page_index, toc_page_count
    )
    corrected: List[TocEntry] = []

    for index, entry in enumerate(entries):
        printed = entry.page_number
        label = (entry.page_label or "").strip()
        if not label and printed > 0:
            label = str(printed)

        link_page = link_targets[index] if index < len(link_targets) else 0
        outline_page = _match_outline_page(entry.title, outline_entries)

        # Prefer trustworthy printed/layout numbers (handles dual-label TOCs
        # where the larger value is already the post-insertion PDF page).
        # Links/outline are fallbacks when printed numbers are unreliable.
        page_number = printed
        if printed_ok and printed > 0 and printed not in toc_pages:
            page_number = printed
        elif link_page > 0 and link_page not in toc_pages:
            page_number = link_page
        elif outline_page > 0 and outline_page not in toc_pages:
            page_number = outline_page
        elif link_page > 0:
            page_number = link_page
        elif outline_page > 0:
            page_number = outline_page

        corrected.append(
            TocEntry(
                title=entry.title,
                page_number=page_number,
                level=entry.level,
                page_label=label or (str(printed) if printed > 0 else ""),
            )
        )

    if (
        outline_entries
        and _entries_point_at_toc_pages(corrected, toc_page_index, toc_page_count)
        and not printed_ok
    ):
        return [
            TocEntry(
                title=e.title,
                page_number=e.page_number,
                level=e.level,
                page_label=e.page_label or str(e.page_number),
            )
            for e in outline_entries
        ]

    return corrected


def _dedupe_overlapping_words(words: List[dict], x_tol: float = 2.0, y_tol: float = 2.0) -> List[dict]:
    """
    Drop duplicate glyphs from bold-overprint / double-draw PDFs.

    Keeps the first word when another has nearly the same position and text.
    """
    if not words:
        return []
    kept: List[dict] = []
    for word in sorted(words, key=lambda w: (float(w.get("top", 0.0)), float(w.get("x0", 0.0)))):
        text = (word.get("text") or "").strip()
        x0 = float(word.get("x0", 0.0))
        top = float(word.get("top", 0.0))
        duplicate = False
        for prev in kept:
            if (prev.get("text") or "").strip() != text:
                continue
            if abs(float(prev.get("x0", 0.0)) - x0) <= x_tol and abs(
                float(prev.get("top", 0.0)) - top
            ) <= y_tol:
                duplicate = True
                break
        if not duplicate:
            kept.append(word)
    return kept


def _collapse_duplicated_tokens(tokens: List[str]) -> List[str]:
    """Collapse runs of identical tokens produced by double-drawn text (e.g. 2,2 -> 2)."""
    if not tokens:
        return []
    collapsed: List[str] = [tokens[0]]
    for token in tokens[1:]:
        if token == collapsed[-1]:
            continue
        collapsed.append(token)
    return collapsed


def _normalize_toc_page_label(label: str) -> Tuple[str, Optional[int]]:
    """
    Clean a joined TOC page label and extract a navigable integer.

    Handles hyphen-wrapped forms (``- 2 -``) and doubled glyphs (``- - 2 2 - -``).
    Also rejoins split digit glyphs (``5`` ``0`` → ``50``).

    When a TOC line shows two page numbers from a stacked update (e.g.
    ``- - 13 10 - -`` or ``- 10 - - 13 -``), the larger value is the current
    page after front-matter insertion; the smaller is the pre-insertion original.
    """
    cleaned = re.sub(r"\s+", " ", (label or "").strip())
    if not cleaned:
        return "", None

    tokens = _collapse_duplicated_tokens(cleaned.split(" "))
    # Rejoin adjacent SINGLE digit glyphs only ("5" "0" -> "50").
    # Do not glue separate page numbers like "13" "10" into "1310".
    merged: List[str] = []
    for token in tokens:
        if (
            merged
            and re.fullmatch(r"\d", merged[-1])
            and re.fullmatch(r"\d", token)
        ):
            merged[-1] = merged[-1] + token
        else:
            merged.append(token)
    cleaned = " ".join(merged).strip()

    # Dual / stacked TOC page numbers (current + pre-insertion original).
    # Only hyphens and integers — take the larger (current) page.
    if merged and all(
        HYPHEN_ONLY_RE.fullmatch(t) or re.fullmatch(r"\d+", t) for t in merged
    ):
        ints = [int(t) for t in merged if re.fullmatch(r"\d+", t)]
        if len(ints) >= 2:
            number = max(ints)
            return f"- {number} -", number
        if len(ints) == 1:
            number = ints[0]
            if any(HYPHEN_ONLY_RE.fullmatch(t) for t in merged):
                return f"- {number} -", number
            return str(number), number

    # Canonical hyphen-wrapped integer: - 12 -
    wrapped = re.fullmatch(
        r"[\-\u2013]+\s*(\d+)\s*[\-\u2013]+",
        cleaned,
    )
    if wrapped:
        number = int(wrapped.group(1))
        return f"- {number} -", number

    # Plain integer (possibly with a single leading/trailing hyphen glued on)
    plain = _parse_page_number_token(cleaned)
    if plain is not None:
        return str(plain), plain

    # "Page 12" / "Seite 12"
    labeled = re.fullmatch(
        r"(?i)(page|seite)\s+(\d+)\b(.*)$",
        cleaned,
    )
    if labeled:
        number = int(labeled.group(2))
        suffix = labeled.group(3).strip()
        core = f"{labeled.group(1).capitalize()} {number}"
        return (f"{core}{suffix}" if suffix else core), number

    # Complex stamps like 10A.9.14 — keep text; nav = last integer run when sensible
    if re.fullmatch(r"\d+[A-Za-z]?(?:\.\d+)+", cleaned) or re.fullmatch(
        r"(?i)(?:page|seite)\s+\d+[A-Za-z0-9.\-]*", cleaned
    ):
        digits = re.findall(r"\d+", cleaned)
        nav = int(digits[-1]) if digits else None
        return cleaned, nav

    # Reject codes like "G11" that are not TOC page numbers.
    return "", None


def _looks_like_page_label_fragment(text: str) -> bool:
    """True for TOC/footer fragments such as '12', '- 12 -', 'Page', 'Seite'."""
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    if cleaned.lower() in {"page", "seite", "p.", "s."}:
        return True
    if HYPHEN_ONLY_RE.fullmatch(cleaned):
        return True
    if _parse_page_number_token(cleaned) is not None:
        return True
    # Complex stamps: require a digit and a separator/letter mix, not bare "G11".
    if re.fullmatch(r"\d+[A-Za-z]?(?:\.\d+)+", cleaned):
        return True
    if re.fullmatch(r"\d+[A-Za-z]\d*(?:\.\d+)*", cleaned):
        return True
    return False


def _line_has_right_page_number(
    line_words: List[dict],
    page_width: float,
) -> Tuple[Optional[List[dict]], Optional[str], Optional[int]]:
    """
    Return (label_words, label_text, navigation_int) for a right-column TOC page label.

    Supports plain integers, hyphen-wrapped ``- 12 -``, and richer stamps such as
    ``10A.9.14`` or ``Page 12``. Rejects header codes like ``G11``.
    """
    if not line_words:
        return None, None, None

    line_words = _dedupe_overlapping_words(line_words)

    threshold = page_width * 0.55 if page_width else 0.0
    right_words = [
        word
        for word in line_words
        if not page_width or float(word["x0"]) >= threshold
    ]
    if not right_words:
        right_words = line_words[-3:]

    label_words: List[dict] = []
    for word in reversed(right_words):
        text = (word.get("text") or "").strip()
        if _looks_like_page_label_fragment(text):
            label_words.insert(0, word)
            continue
        if label_words:
            break

    if not label_words:
        return None, None, None

    # Drop leading leader dots that slipped into the right band.
    while label_words and re.fullmatch(
        r"[.\u00b7·]{2,}", (label_words[0].get("text") or "").strip()
    ):
        label_words = label_words[1:]
    if not label_words:
        return None, None, None

    raw_label = " ".join((w.get("text") or "").strip() for w in label_words).strip()
    label, nav = _normalize_toc_page_label(raw_label)
    if not label:
        return None, None, None
    return label_words, label, nav


def _strip_trailing_page_label(title: str, page_label: str) -> str:
    """Remove a trailing TOC page label from a title when it was left attached."""
    title = (title or "").strip()
    label = (page_label or "").strip()
    if not title or not label:
        return title
    # Exact trailing label
    pattern = re.compile(
        rf"^(?P<head>.*?)\s+{re.escape(label)}\s*$",
        re.IGNORECASE,
    )
    match = pattern.match(title)
    if match and match.group("head").strip():
        return match.group("head").strip()
    # Trailing bare integer often left behind when label parsing failed earlier.
    bare = re.match(r"^(?P<head>.*?)\s+(?P<num>\d+)\s*$", title)
    if bare and bare.group("head").strip():
        head = bare.group("head").strip()
        # Avoid chopping dotted chapter tails that somehow remain in the title body.
        if not re.search(r"\d+\.\d+$", head):
            return head
    return title


def _title_from_toc_line_words(
    line_words: List[dict],
    label_words: Optional[List[dict]] = None,
    page_number: Optional[int] = None,
) -> str:
    """Build the title text from a TOC line, stripping leaders and page-number wrappers."""
    skip_ids = {id(word) for word in (label_words or [])}
    title_words = []
    for word in line_words:
        if id(word) in skip_ids:
            continue
        if label_words and HYPHEN_ONLY_RE.fullmatch(word["text"].strip()):
            leftmost = min(float(w["x0"]) for w in label_words)
            if float(word["x0"]) >= leftmost - 30:
                continue
        if (
            page_number is not None
            and _parse_page_number_token(word["text"]) == page_number
            and label_words
            and float(word["x0"]) >= min(float(w["x0"]) for w in label_words) - 5
        ):
            continue
        if re.fullmatch(r"[.\u00b7·\-_]{2,}", word["text"].strip()):
            continue
        # Skip runs of single-dot leader glyphs.
        if re.fullmatch(r"[\.\u00b7·]", word["text"].strip()):
            continue
        title_words.append(word)

    title = " ".join(word["text"] for word in title_words).strip()
    title = re.sub(r"\s+", " ", title)
    title = re.sub(r"[\.\u00b7·…\s]+$", "", title).strip()
    return title


def _looks_like_toc_entry_start(title: str) -> bool:
    """True when a line without a page number still looks like a TOC entry / wrap start."""
    stripped = (title or "").strip()
    if len(stripped) < 2:
        return False
    if re.fullmatch(r"[.\u00b7·\-_\s]+", stripped):
        return False
    if _is_running_footer_text(stripped):
        return False
    return True


def _is_running_footer_text(text: str) -> bool:
    """
    True for document running-footer lines that must never become TOC entries.

    Long footers often place a date (e.g. ``Stand 10.10.2025``) in the right
    half of the page, which otherwise looks like a TOC page number.
    """
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return False
    lower = cleaned.lower()
    if re.search(r"\bstand\s+\d{1,2}[./]\d{1,2}[./]\d{2,4}\b", lower):
        return True
    if re.search(r"\b\d{1,2}[./]\d{1,2}[./]\d{4}\b", cleaned) and len(cleaned) > 40:
        return True
    # Project / chapter stamp lines without TOC leaders.
    if "statische berechnungen" in lower:
        return True
    if re.match(r"^[a-z]{2,5}\s*[–\-]\s+\S", lower) and len(cleaned) < 60:
        # Short project codes such as "ZRH – Zentrales Grüezi".
        if "….. " not in cleaned and "...." not in cleaned and "··" not in cleaned:
            if not CHAPTER_NUMBER_RE.match(cleaned):
                return True
    return False


def _is_in_page_footer_band(top: float, page_height: float) -> bool:
    """True when a line sits in the bottom ~12% of the page (running footer)."""
    if page_height <= 0:
        return False
    return top >= page_height * 0.88


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

    Hierarchy comes primarily from dotted chapter numbers (1 / 1.2 / 1.2.3) and
    secondarily from relative left indentation on the TOC page.
    """
    if toc_page_index < 0:
        return []

    # (left, title, page_number, page_label) collected in reading order
    raw_rows: List[Tuple[float, str, int, str]] = []
    pending_title = ""
    pending_left = 0.0
    seen_toc_title = False

    try:
        with pdfplumber.open(pdf_path) as pdf:
            last_index = min(len(pdf.pages), toc_page_index + max(1, toc_page_count))
            for page_index in range(toc_page_index, last_index):
                page = pdf.pages[page_index]
                words = _dedupe_overlapping_words(
                    page.extract_words(extra_attrs=["size", "fontname"]) or []
                )
                page_width = float(page.width or 0.0)
                page_height = float(page.height or 0.0)

                for line_words in _group_words_into_lines(words):
                    line_words = sorted(line_words, key=lambda item: item["x0"])
                    if not line_words:
                        continue

                    line_text = " ".join(word["text"] for word in line_words).strip()
                    if _is_toc_title_text(line_text):
                        pending_title = ""
                        seen_toc_title = True
                        continue

                    has_leaders = any(
                        re.fullmatch(r"[.\u00b7·]{2,}", (w.get("text") or "").strip())
                        for w in line_words
                    )

                    # Skip leftover header/footer bands on multi-page TOCs.
                    top = float(line_words[0].get("top", 0.0))
                    if _is_in_page_footer_band(top, page_height):
                        continue
                    if (
                        page_height
                        and top < page_height * 0.08
                        and not has_leaders
                        and not seen_toc_title
                    ):
                        continue

                    if _is_running_footer_text(line_text):
                        continue

                    label_words, page_label, page_number = _line_has_right_page_number(
                        line_words, page_width
                    )

                    # Skip running header/footer stamps (e.g. "Page 10A.9.03").
                    if page_label and re.match(
                        r"(?i)(?:page|seite)\s+\d+[A-Za-z]", page_label.strip()
                    ):
                        continue
                    if page_label and re.fullmatch(
                        r"\d+[A-Za-z](?:\.\d+)+", page_label.strip()
                    ):
                        continue
                    # Dates mistaken for page numbers (e.g. trailing 2025).
                    if page_label and re.fullmatch(r"20\d{2}", page_label.strip()):
                        continue
                    if (
                        page_label
                        and not has_leaders
                        and re.search(r"\bstand\b", line_text, re.IGNORECASE)
                    ):
                        continue

                    # Ignore running headers above the TOC heading unless this
                    # line already looks like a real TOC row (leaders + page #).
                    if not seen_toc_title:
                        if has_leaders and page_label:
                            seen_toc_title = True
                        else:
                            continue

                    title = _title_from_toc_line_words(
                        line_words, label_words, page_number
                    )

                    left = float(line_words[0]["x0"])

                    if page_number is None and not page_label:
                        # Title wrap / first line of a multi-line TOC entry.
                        if _looks_like_toc_entry_start(title):
                            if CHAPTER_NUMBER_RE.match(title.strip()) or (
                                level_from_chapter_number(title) is not None
                            ):
                                pending_title = title
                                pending_left = left
                            elif pending_title:
                                pending_title = _merge_wrapped_toc_title(
                                    pending_title, title
                                )
                            else:
                                pending_title = title
                                pending_left = left
                        continue

                    row_left = pending_left if pending_title else left
                    full_title = _merge_wrapped_toc_title(pending_title, title)
                    pending_title = ""
                    pending_left = 0.0
                    label = (page_label or "").strip()
                    if page_number is None and label:
                        _, page_number = _normalize_toc_page_label(label)
                        page_number = page_number or 0
                    full_title = _strip_trailing_page_label(
                        full_title, label or str(page_number or "")
                    )
                    if len(full_title) < 2:
                        continue

                    raw_rows.append(
                        (row_left, full_title, int(page_number or 0), label)
                    )
    except Exception:
        return []

    if not raw_rows:
        return []

    indent_levels = _levels_from_relative_indents([row[0] for row in raw_rows])
    entries: List[TocEntry] = []
    for (left, title, page_number, page_label), indent_level in zip(
        raw_rows, indent_levels
    ):
        number_level = level_from_chapter_number(title)
        level = number_level if number_level is not None else indent_level
        cleaned_title = _strip_trailing_page_label(title, page_label or str(page_number))
        entries.append(
            TocEntry(
                title=cleaned_title,
                page_number=page_number,
                level=max(1, level),
                page_label=page_label or str(page_number),
            )
        )
    return apply_hierarchy_levels(entries)


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
        if _is_running_footer_text(line):
            pending_title = ""
            pending_indent = ""
            continue

        match = TOC_ENTRY_RE.match(line)
        if match:
            indent, title, page_str = match.groups()
            # Reject date years / footer leftovers parsed as page numbers.
            if re.fullmatch(r"20\d{2}", page_str) and not re.search(
                r"[.\u00b7·]{2,}", line
            ):
                continue
            if _is_running_footer_text(title):
                continue
            full_title = _merge_wrapped_toc_title(pending_title, title.strip())
            pending_title = ""
            pending_indent = ""
            number_level = level_from_chapter_number(full_title)
            if number_level is not None:
                level = number_level
            else:
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


def _entries_from_outline(
    outline_entries: List[TocEntry],
    page_entries: List[TocEntry],
) -> List[TocEntry]:
    """
    Build canonical TOC rows from the PDF sidebar outline.

    Word-exported heading bookmarks carry the correct titles and nesting.
    Layout TOC text is used only to attach printed page labels when a row
    can be matched — never for titles or hierarchy.
    """
    if not outline_entries:
        return []

    enriched: List[TocEntry] = []
    for entry in outline_entries:
        page_label = (entry.page_label or "").strip()
        if not page_label and page_entries:
            matched = _match_outline_entry(entry.title, page_entries)
            if matched is not None:
                page_label = (matched.page_label or "").strip()
                if not page_label and matched.page_number > 0:
                    page_label = str(matched.page_number)
        if not page_label and entry.page_number > 0:
            page_label = str(entry.page_number)
        enriched.append(
            TocEntry(
                title=entry.title,
                page_number=entry.page_number,
                level=entry.level,
                page_label=page_label,
            )
        )
    return enriched


def extract_toc_from_pdf(pdf_path: str) -> TocInfo:
    """Extract TOC entries and TOC page metadata from a PDF."""
    reader = PdfReader(pdf_path, strict=False)
    if not reader.pages:
        return TocInfo()

    width, height = _page_size(reader, 0)
    outline_entries = _extract_outline_entries(reader)
    toc_page_index, page_entries, style, toc_page_count = _extract_toc_page_entries(reader)

    # Layout extraction helps printed page labels and anchor positions only.
    layout_entries = _extract_toc_entries_from_layout(
        pdf_path, toc_page_index, max(1, toc_page_count or 1)
    )
    if layout_entries:
        page_entries = layout_entries

    entries: List[TocEntry]
    source = ""
    if outline_entries:
        # PDF sidebar (Word heading bookmarks) is authoritative for titles
        # and hierarchy. Layout TOC parsing can pick up footer noise.
        entries = _entries_from_outline(outline_entries, page_entries)
        source = "outline"
    elif page_entries:
        link_targets = _extract_toc_link_targets(
            reader, toc_page_index, max(1, toc_page_count or 1)
        )
        entries = _correct_entry_page_numbers(
            page_entries,
            outline_entries,
            link_targets,
            toc_page_index,
            toc_page_count or 1,
        )
        source = "toc_page"
        entries = _enrich_levels_from_outline(entries, outline_entries)
        entries = apply_hierarchy_levels(entries)
    else:
        return TocInfo(page_width=width, page_height=height)

    if toc_page_index >= 0:
        page_width, page_height = _page_size(reader, toc_page_index)
    else:
        page_width, page_height = width, height

    anchors: List[TocPageNumberAnchor] = []
    if toc_page_index >= 0 and entries:
        anchors = _extract_page_number_anchors(
            pdf_path,
            toc_page_index,
            entries,
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
                info.entries = apply_hierarchy_levels(resolved)
                info.source = "docx_headings"

    return info


def build_toc_overlay_pdf(
    toc_info: TocInfo,
    adjusted_entries: List[TocEntry],
    output_path: str,
    label_overrides: Optional[Dict[int, str]] = None,
    label_colors: Optional[Dict[int, Tuple[int, int, int]]] = None,
    label_color_suffixes: Optional[Dict[int, str]] = None,
) -> bool:
    """
    Build a multi-page transparent overlay — one page per TOC sheet — that covers
    old page numbers and draws the adjusted ones.

    ``label_overrides`` maps TOC entry index -> exact label text (e.g. \"12A\").
    When omitted, labels are derived from ``adjusted_entries`` page numbers.
    ``label_colors`` maps TOC entry index -> RGB font colour for replacement text.
    ``label_color_suffixes`` maps entry index -> trailing substring that alone
    should use ``label_colors`` (the prefix, e.g. original ``- 12 -``, stays black).
    Old page numbers are wiped with opaque white first so new labels fully replace them.
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
    overrides = label_overrides or {}
    colors = label_colors or {}
    color_suffixes = label_color_suffixes or {}

    for offset in range(page_count):
        page_anchors = [
            anchor for anchor in toc_info.anchors if anchor.toc_page_offset == offset
        ]
        for anchor in page_anchors:
            if anchor.entry_index >= len(adjusted_entries):
                continue
            if anchor.entry_index in overrides:
                new_label = overrides[anchor.entry_index]
            else:
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

            pad_x = max(10.0, font_size * 0.75)
            pad_y = max(4.0, font_size * 0.35)
            # Wipe must cover the original digits and the (possibly wider) new label.
            wipe_left = min(anchor.x0, draw_x) - pad_x
            wipe_right = max(anchor.x1, draw_x + text_width) + pad_x
            wipe_bottom = min(baseline_y - pad_y, height - anchor.bottom - pad_y)
            wipe_top = max(
                baseline_y + font_size + pad_y,
                height - anchor.top + pad_y,
            )

            # Opaque white erase — removes underlying page numbers completely.
            c.setFillColor(white)
            c.rect(
                wipe_left,
                wipe_bottom,
                max(1.0, wipe_right - wipe_left),
                max(1.0, wipe_top - wipe_bottom),
                fill=1,
                stroke=0,
            )

            rgb = colors.get(anchor.entry_index)
            suffix = (color_suffixes.get(anchor.entry_index) or "").strip()
            if (
                rgb is not None
                and suffix
                and new_label.endswith(suffix)
                and len(new_label) > len(suffix)
            ):
                prefix = new_label[: -len(suffix)]
                c.setFillColor("black")
                if prefix:
                    c.drawString(draw_x, baseline_y, prefix)
                suffix_x = draw_x + c.stringWidth(prefix, font_name, font_size)
                c.setFillColorRGB(
                    max(0, min(255, int(rgb[0]))) / 255.0,
                    max(0, min(255, int(rgb[1]))) / 255.0,
                    max(0, min(255, int(rgb[2]))) / 255.0,
                )
                c.drawString(suffix_x, baseline_y, suffix)
            elif rgb is not None:
                c.setFillColorRGB(
                    max(0, min(255, int(rgb[0]))) / 255.0,
                    max(0, min(255, int(rgb[1]))) / 255.0,
                    max(0, min(255, int(rgb[2]))) / 255.0,
                )
                c.drawString(draw_x, baseline_y, new_label)
            else:
                c.setFillColor("black")
                c.drawString(draw_x, baseline_y, new_label)

        c.showPage()

    c.save()
    return True


def apply_toc_bookmarks(
    pdf_path: str,
    entries: List[TocEntry],
    output_path: Optional[str] = None,
) -> bool:
    """
    Write a hierarchical PDF outline (sidebar bookmarks) from TOC entries.

    Entry ``level`` values become nesting; ``page_number`` is 1-based and must
    already reflect any insertions. Existing bookmarks are replaced.
    """
    if not entries:
        return False

    try:
        import pikepdf
        from pikepdf import OutlineItem
    except ImportError as exc:
        raise ImportError(
            "pikepdf is required to write PDF bookmarks. "
            "Install it with: pip install pikepdf"
        ) from exc

    import os
    import tempfile

    dest_path = output_path or pdf_path
    if not _entries_have_outline_hierarchy(entries):
        entries = apply_hierarchy_levels(entries)
    wrote_any = False

    # Always save via a temp file, then replace — Windows cannot overwrite a
    # path that pikepdf still has open.
    fd, temp_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        with pikepdf.Pdf.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
            if page_count == 0:
                return False

            with pdf.open_outline() as outline:
                outline.root.clear()
                stack: List[Tuple[int, OutlineItem]] = []

                for entry in entries:
                    if entry.page_number <= 0:
                        continue
                    page_index = min(page_count - 1, max(0, entry.page_number - 1))
                    title = (entry.title or "").strip() or f"Page {entry.page_number}"
                    item = OutlineItem(title, page_index)
                    level = max(1, int(entry.level or 1))

                    while stack and stack[-1][0] >= level:
                        stack.pop()
                    if stack:
                        stack[-1][1].children.append(item)
                    else:
                        outline.root.append(item)
                    stack.append((level, item))
                    wrote_any = True

            if not wrote_any:
                return False

            pdf.save(temp_path)

        os.replace(temp_path, dest_path)
        temp_path = ""  # ownership transferred
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass

    return True


def update_toc_page_in_place(
    pdf_path: str,
    toc_info: TocInfo,
    insertion_counts: List[Tuple[int, int]],
    output_path: str,
    temp_dir: str,
    merge_stamp_on_top=None,
    label_overrides: Optional[Dict[int, str]] = None,
    label_colors: Optional[Dict[int, Tuple[int, int, int]]] = None,
    label_color_suffixes: Optional[Dict[int, str]] = None,
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
    if not build_toc_overlay_pdf(
        toc_info,
        adjusted_entries,
        overlay_path,
        label_overrides=label_overrides,
        label_colors=label_colors,
        label_color_suffixes=label_color_suffixes,
    ):
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


def _collect_link_destinations(
    pdf_path: str,
) -> Dict[int, List[Tuple[Tuple[float, float, float, float], int]]]:
    """
    Collect GoTo link annotations: source_page_index -> [(rect, dest_0based), ...].

    Order matches the PDF Annots array (used to align with the swapped output).
    """
    reader = PdfReader(pdf_path, strict=False)
    result: Dict[int, List[Tuple[Tuple[float, float, float, float], int]]] = {}

    for page_index, page in enumerate(reader.pages):
        annots = page.get("/Annots")
        if not annots:
            continue
        try:
            annot_list = list(annots)
        except Exception:
            continue

        entries: List[Tuple[Tuple[float, float, float, float], int]] = []
        for annot_ref in annot_list:
            try:
                annot = (
                    annot_ref.get_object()
                    if hasattr(annot_ref, "get_object")
                    else annot_ref
                )
            except Exception:
                continue
            subtype = str(annot.get("/Subtype", ""))
            if subtype not in {"/Link", "Link"}:
                continue

            dest = annot.get("/Dest")
            action = annot.get("/A")
            if action is not None:
                try:
                    action = (
                        action.get_object()
                        if hasattr(action, "get_object")
                        else action
                    )
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
                rect_t = (
                    float(rect[0]),
                    float(rect[1]),
                    float(rect[2]),
                    float(rect[3]),
                )
            except Exception:
                rect_t = (0.0, 0.0, 0.0, 0.0)

            entries.append((rect_t, dest_page - 1))

        if entries:
            result[page_index] = entries

    return result


def remap_link_destinations(
    source_pdf_path: str,
    pdf_path: str,
    old_to_new: Dict[int, int],
    output_path: Optional[str] = None,
    replaced_page_indices: Optional[set] = None,
) -> bool:
    """
    Rewrite GoTo link destinations after pages were swapped/reordered.

    PyPDF2 assembly leaves Link ``/Dest`` arrays pointing at removed page
    objects. This remaps each surviving page's links using destinations
    resolved from ``source_pdf_path`` and ``old_to_new`` (0-based indices).

    ``replaced_page_indices`` are source pages that no longer exist (skipped).
    """
    try:
        import pikepdf
        from pikepdf import Array, Name
    except ImportError as exc:
        raise ImportError(
            "pikepdf is required to remap PDF link destinations. "
            "Install it with: pip install pikepdf"
        ) from exc

    import os
    import tempfile

    source_links = _collect_link_destinations(source_pdf_path)
    if not source_links:
        return False

    replaced = replaced_page_indices or set()
    dest_path = output_path or pdf_path
    updated = 0

    fd, temp_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        with pikepdf.Pdf.open(pdf_path) as pdf:
            for src_page, links in source_links.items():
                if src_page in replaced:
                    continue
                out_page_index = old_to_new.get(src_page)
                if out_page_index is None:
                    continue
                if out_page_index < 0 or out_page_index >= len(pdf.pages):
                    continue

                page = pdf.pages[out_page_index]
                raw_annots = page.get("/Annots")
                if raw_annots is None:
                    continue

                link_annots = []
                for annot in raw_annots:
                    try:
                        subtype = str(annot.get("/Subtype", ""))
                    except Exception:
                        continue
                    if subtype in {"/Link", "Link"}:
                        link_annots.append(annot)

                if not link_annots:
                    continue

                # Prefer 1:1 order match; fall back to nearest rect.
                for link_idx, (rect, old_dest_idx) in enumerate(links):
                    new_dest_idx = old_to_new.get(old_dest_idx)
                    if new_dest_idx is None:
                        continue
                    if new_dest_idx < 0 or new_dest_idx >= len(pdf.pages):
                        continue

                    annot = None
                    if link_idx < len(link_annots):
                        annot = link_annots[link_idx]
                    else:
                        # Rect fallback when annot counts diverge.
                        best = None
                        best_dist = None
                        for candidate in link_annots:
                            try:
                                crect = candidate.get("/Rect")
                                dist = (
                                    abs(float(crect[0]) - rect[0])
                                    + abs(float(crect[1]) - rect[1])
                                    + abs(float(crect[2]) - rect[2])
                                    + abs(float(crect[3]) - rect[3])
                                )
                            except Exception:
                                continue
                            if best_dist is None or dist < best_dist:
                                best_dist = dist
                                best = candidate
                        annot = best

                    if annot is None:
                        continue

                    target_page = pdf.pages[new_dest_idx]
                    # Preserve XYZ / FitR view args when present.
                    view_args: List[Any] = [Name("/Fit")]
                    existing = None
                    try:
                        if annot.get("/Dest") is not None:
                            existing = annot.Dest
                        elif annot.get("/A") is not None:
                            action = annot.A
                            if str(action.get("/S", "")) in {"/GoTo", "GoTo"}:
                                existing = action.get("/D")
                    except Exception:
                        existing = None

                    if existing is not None:
                        try:
                            items = list(existing)
                            if len(items) >= 2:
                                view_args = items[1:]
                        except Exception:
                            view_args = [Name("/Fit")]

                    new_dest = Array([target_page.obj, *view_args])
                    try:
                        if annot.get("/Dest") is not None:
                            annot.Dest = new_dest
                            updated += 1
                        elif annot.get("/A") is not None:
                            action = annot.A
                            if str(action.get("/S", "")) in {"/GoTo", "GoTo"}:
                                action.D = new_dest
                                updated += 1
                            else:
                                # Force an explicit Dest GoTo.
                                annot.Dest = new_dest
                                updated += 1
                        else:
                            annot.Dest = new_dest
                            updated += 1
                    except Exception:
                        continue

            if updated <= 0:
                return False

            pdf.save(temp_path)

        os.replace(temp_path, dest_path)
        temp_path = ""
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass

    return updated > 0
