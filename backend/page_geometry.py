"""
Page size and orientation classification for uploaded documents.

Used to group the pages of a report into "page types" (e.g. A4 Portrait,
A3 Landscape) so page-number positions can be configured per type.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

MM_TO_POINTS = 72.0 / 25.4
POINTS_TO_CM = 2.54 / 72.0

# Scanned/converted pages drift by a fraction of a millimetre from nominal sizes.
SIZE_TOLERANCE_MM = 3.0

ORIENTATION_PORTRAIT = "Portrait"
ORIENTATION_LANDSCAPE = "Landscape"
ORIENTATION_SQUARE = "Square"

# Nominal short x long edge in millimetres.
STANDARD_PAGE_SIZES_MM: Dict[str, Tuple[float, float]] = {
    "A0": (841.0, 1189.0),
    "A1": (594.0, 841.0),
    "A2": (420.0, 594.0),
    "A3": (297.0, 420.0),
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
    "A6": (105.0, 148.0),
    "B4": (250.0, 353.0),
    "B5": (176.0, 250.0),
    "Letter": (215.9, 279.4),
    "Legal": (215.9, 355.6),
    "Ledger": (279.4, 431.8),
    "Executive": (184.1, 266.7),
}

_KEY_SEPARATOR = "|"


@dataclass(frozen=True, order=True)
class PageType:
    """A page size name plus orientation, used as a settings bucket key."""

    size_name: str
    orientation: str

    @property
    def label(self) -> str:
        """Human-readable tab label, e.g. ``"A4 Portrait"``."""
        if self.orientation == ORIENTATION_SQUARE:
            return self.size_name
        return f"{self.size_name} {self.orientation}"

    @property
    def key(self) -> str:
        """Stable string form for session storage."""
        return f"{self.size_name}{_KEY_SEPARATOR}{self.orientation}"


@dataclass
class PageTypeInfo:
    """A detected page type with representative dimensions and a page tally."""

    page_type: PageType
    # Dimensions as displayed in a viewer (after /Rotate), in PDF points.
    width_pt: float
    height_pt: float
    page_count: int

    @property
    def label(self) -> str:
        return self.page_type.label

    @property
    def width_cm(self) -> float:
        return self.width_pt * POINTS_TO_CM

    @property
    def height_cm(self) -> float:
        return self.height_pt * POINTS_TO_CM


def page_type_from_key(key: str) -> Optional[PageType]:
    """Parse a :attr:`PageType.key` string back into a ``PageType``."""
    if not key or _KEY_SEPARATOR not in key:
        return None
    size_name, orientation = key.split(_KEY_SEPARATOR, 1)
    if not size_name or not orientation:
        return None
    return PageType(size_name, orientation)


def display_dimensions(
    width_pt: float, height_pt: float, rotation: int = 0
) -> Tuple[float, float]:
    """Width/height as shown in a viewer, swapping axes for 90/270 rotation."""
    if rotation % 180 == 90:
        return height_pt, width_pt
    return width_pt, height_pt


def _match_standard_size(short_mm: float, long_mm: float) -> Optional[str]:
    """Closest standard size name whose edges are both within tolerance."""
    best_name: Optional[str] = None
    best_error = SIZE_TOLERANCE_MM * 2
    for name, (nominal_short, nominal_long) in STANDARD_PAGE_SIZES_MM.items():
        short_error = abs(short_mm - nominal_short)
        long_error = abs(long_mm - nominal_long)
        if short_error > SIZE_TOLERANCE_MM or long_error > SIZE_TOLERANCE_MM:
            continue
        error = short_error + long_error
        if error < best_error:
            best_name = name
            best_error = error
    return best_name


def classify_page(
    width_pt: float, height_pt: float, rotation: int = 0
) -> PageType:
    """
    Classify one page into a size name and orientation.

    Rotation is applied first, so a portrait A4 page with ``/Rotate 90`` is
    reported as A4 Landscape — matching what the reader sees.
    """
    disp_w, disp_h = display_dimensions(width_pt, height_pt, rotation)
    width_mm = disp_w / MM_TO_POINTS
    height_mm = disp_h / MM_TO_POINTS

    if abs(width_mm - height_mm) <= SIZE_TOLERANCE_MM:
        orientation = ORIENTATION_SQUARE
    elif width_mm > height_mm:
        orientation = ORIENTATION_LANDSCAPE
    else:
        orientation = ORIENTATION_PORTRAIT

    short_mm, long_mm = sorted((width_mm, height_mm))
    size_name = _match_standard_size(short_mm, long_mm)
    if size_name is None:
        size_name = f"{short_mm:.0f}×{long_mm:.0f} mm"
    return PageType(size_name, orientation)


def summarize_page_types(
    dimensions: Iterable[Tuple[float, float, int]]
) -> List[PageTypeInfo]:
    """
    Group ``(width_pt, height_pt, rotation)`` triples into detected page types.

    Ordered by page count descending so the dominant body size comes first,
    with the type label as a tie-break for stable tab ordering.
    """
    buckets: Dict[PageType, PageTypeInfo] = {}
    for width_pt, height_pt, rotation in dimensions:
        if width_pt <= 0 or height_pt <= 0:
            continue
        page_type = classify_page(width_pt, height_pt, rotation)
        existing = buckets.get(page_type)
        if existing is None:
            disp_w, disp_h = display_dimensions(width_pt, height_pt, rotation)
            buckets[page_type] = PageTypeInfo(
                page_type=page_type,
                width_pt=disp_w,
                height_pt=disp_h,
                page_count=1,
            )
        else:
            existing.page_count += 1

    return sorted(
        buckets.values(),
        key=lambda info: (-info.page_count, info.label),
    )
