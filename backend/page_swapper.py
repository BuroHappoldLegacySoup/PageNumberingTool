"""
Replace selected pages in a main PDF with pages from other PDFs.

Swapped-in pages keep the original page-number text and append a suffix:
  - first replacement page  ->  \"{original}{letter}\"
  - extra pages             ->  \"{original}-1\", \"{original}-2\", ...
    (extra indices can be zero-padded via ``num_digits``)
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Pattern, Tuple

from backend.page_number_config import (
    DEFAULT_FONT,
    DEFAULT_FONT_SIZE,
    DEFAULT_ORIGIN,
    DEFAULT_POSITION,
    DEFAULT_SEPARATOR,
    POSITION_ABSOLUTE,
    POSITION_PRESETS,
    POSITION_RELATIVE,
    PageNumberSettings,
    format_page_number_text,
    preset_anchor,
)
from backend.page_spec import InsertionSegment, parse_pages_spec
from backend.toc_handler import (
    TocEntry,
    TocInfo,
    apply_hierarchy_levels,
    apply_toc_bookmarks,
    remap_link_destinations,
    update_toc_page_in_place,
    _extract_outline_entries,
    _group_words_into_lines,
)


# User-facing page-number locations on the body page (pdfplumber: y grows downward).
PAGE_NUMBER_POSITIONS: Dict[str, str] = {
    "bottom_centre": "Bottom Centre",
    "bottom_left": "Bottom Left",
    "bottom_right": "Bottom Right",
    "top_centre": "Top Centre",
    "top_left": "Top Left",
    "top_right": "Top Right",
}

# Reverse map: Inserter preset display name -> swapper position key
POSITION_NAME_TO_KEY: Dict[str, str] = {
    label: key for key, label in PAGE_NUMBER_POSITIONS.items()
}

DEFAULT_PAGE_NUMBER_POSITION = "bottom_centre"


@dataclass
class SwapOperation:
    """Replace one or more 1-based pages in the main PDF with pages from a source PDF."""

    target_pages_spec: str  # e.g. "18" or "18-20" or "18,19,20"
    source_path: str
    source_pages_spec: str = ""  # empty = all pages of source
    # Full stamp text per replacement page (e.g. Page 10A.9.45C). Empty = auto.
    custom_labels: List[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "target_pages_spec": self.target_pages_spec,
            "source_path": self.source_path,
            "source_pages_spec": self.source_pages_spec,
            "custom_labels": list(self.custom_labels),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SwapOperation":
        # Backward compatible with older single target_page field.
        spec = str(data.get("target_pages_spec", "") or "").strip()
        if not spec and data.get("target_page") is not None:
            spec = str(int(data.get("target_page", 1)))
        raw_labels = data.get("custom_labels", data.get("custom_label_suffixes", []))
        labels: List[str] = []
        if isinstance(raw_labels, list):
            labels = [str(item).strip() for item in raw_labels if str(item).strip()]
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex),
            target_pages_spec=spec or "1",
            source_path=str(data.get("source_path", "")),
            source_pages_spec=str(data.get("source_pages_spec", "")),
            custom_labels=labels,
        )

    def target_pages(self, main_page_count: int) -> List[int]:
        return parse_pages_spec(self.target_pages_spec, main_page_count)


def normalize_swap_letter(letter: str) -> str:
    """Return a single A–Z / a–z character, or raise ``ValueError``."""
    cleaned = (letter or "").strip()
    if len(cleaned) != 1 or not cleaned.isalpha():
        raise ValueError("Swap letter must be a single letter (A–Z or a–z)")
    return cleaned


def swap_label(
    base_text: str,
    index_in_replacement: int,
    letter: str = "A",
    num_digits: int = 1,
) -> str:
    """
    Label for an extra/first replacement page.

    index 0 -> ``{base}{letter}``;
    index 1 -> ``{base}-1`` (zero-padded to ``num_digits``); …
    """
    base = (base_text or "").strip() or "?"
    suffix = (letter or "A").strip() or "A"
    digits = max(1, int(num_digits))
    if index_in_replacement <= 0:
        return f"{base}{suffix}"
    return f"{base}-{str(index_in_replacement).zfill(digits)}"


def labels_for_multi_swap(
    target_bases: List[str],
    source_page_count: int,
    letter: str = "A",
    num_digits: int = 1,
) -> List[str]:
    """
    Build stamps when replacing M main pages with N source pages.

    The first ``min(M, N)`` output pages get each original stamp + ``letter``.
    Any extra source pages (N > M) use the last original stamp with ``-1``, ``-2``, …
    """
    if source_page_count < 1:
        return []
    bases = [((b or "").strip() or "?") for b in target_bases] or ["?"]
    m = len(bases)
    labels: List[str] = []
    for i in range(source_page_count):
        if i < m:
            labels.append(swap_label(bases[i], 0, letter=letter, num_digits=num_digits))
        else:
            labels.append(
                swap_label(bases[-1], i - m + 1, letter=letter, num_digits=num_digits)
            )
    return labels


def labels_for_add_pages(
    base_text: str,
    page_count: int,
    num_digits: int = 1,
) -> List[str]:
    """
    Stamps for pages inserted after an existing document page.

    Inserted page i (1-based) -> ``{base}-{i}`` with optional zero-padding.
    """
    if page_count < 1:
        return []
    base = (base_text or "").strip() or "?"
    digits = max(1, int(num_digits))
    return [f"{base}-{str(i).zfill(digits)}" for i in range(1, page_count + 1)]


def format_add_pages_toc_label(
    base_text: str,
    page_count: int,
    range_word: str = "to",
    num_digits: int = 1,
    *,
    wrap_prefix: str = "",
    wrap_suffix: str = "",
) -> str:
    """TOC label for an insert-after page, e.g. ``30 to 30-3``."""
    base = (base_text or "").strip() or "?"
    labels = labels_for_add_pages(base, page_count, num_digits=num_digits)
    if not labels:
        return base
    return format_swap_range_label(
        [base, labels[-1]],
        range_word,
        wrap_prefix=wrap_prefix,
        wrap_suffix=wrap_suffix,
    )


def format_swap_range_label(
    labels: List[str],
    range_word: str = "to",
    *,
    wrap_prefix: str = "",
    wrap_suffix: str = "",
) -> str:
    """
    Build a TOC-facing label from swap stamps.

    One page -> that label; multiple -> ``{first} {word} {last}``.
    Optional hyphen wrappers become ``- 45a to 45-03 -``.
    """
    cleaned = [(lab or "").strip() for lab in labels if (lab or "").strip()]
    if not cleaned:
        return "?"
    word = (range_word or "to").strip() or "to"
    if len(cleaned) == 1:
        text = cleaned[0]
    else:
        text = f"{cleaned[0]} {word} {cleaned[-1]}"
    prefix = (wrap_prefix or "").strip()
    suffix = (wrap_suffix or "").strip()
    if prefix and suffix:
        return f"{prefix} {text} {suffix}"
    return text


def per_page_toc_label_pieces(
    targets: List[int],
    bases: List[str],
    source_page_count: int,
    letter: str,
    num_digits: int,
) -> Dict[int, List[str]]:
    """
    TOC label pieces keyed by main target page.

    Matching pages get a single ``{base}{letter}`` piece.
    When extras exist (N > M), only the last target also includes
    ``{base}-1``, ``{base}-2``, … so its TOC line becomes a short range
    (e.g. ``30C`` / ``30-1`` → ``30C bis 30-1``), while earlier pages stay
    ``26C``, ``27C``, …
    """
    labels = labels_for_multi_swap(
        bases, source_page_count, letter=letter, num_digits=num_digits
    )
    if not targets or not labels:
        return {}
    m = len(targets)
    result: Dict[int, List[str]] = {}
    for i, page in enumerate(targets):
        if i >= len(labels):
            break
        if i == m - 1 and source_page_count > m:
            result[page] = labels[i:]
        else:
            result[page] = [labels[i]]
    return result


def per_page_toc_label_pieces_from_suffixes(
    targets: List[int],
    suffixes: List[str],
) -> Dict[int, List[str]]:
    """Map TOC-style suffixes in replacement order back to main target pages."""
    if not targets or not suffixes:
        return {}
    m = len(targets)
    result: Dict[int, List[str]] = {}
    for i, page in enumerate(targets):
        if i >= len(suffixes):
            break
        if i == m - 1 and len(suffixes) > m:
            result[page] = [s.strip() for s in suffixes[i:] if (s or "").strip()]
        else:
            piece = (suffixes[i] or "").strip()
            if piece:
                result[page] = [piece]
    return result


def toc_suffix_from_full_label(
    body_stamp: str,
    full_label: str,
    digit_base: str,
) -> str:
    """Derive the TOC-style ending (e.g. ``45C``) from a full body stamp label."""
    body = (body_stamp or "").strip()
    full = (full_label or "").strip()
    base = (digit_base or "").strip() or "?"
    if body and full.startswith(body):
        tail = full[len(body) :]
        return f"{base}{tail}" if tail else base
    if base and base in full:
        return full[full.rfind(base) :]
    match = re.search(r"[\w.-]+$", full)
    return match.group(0) if match else full


def per_page_toc_label_pieces_from_full_labels(
    targets: List[int],
    full_labels: List[str],
    bases: List[str],
    toc_info: Optional[TocInfo],
    base_texts: Dict[int, str],
) -> Dict[int, List[str]]:
    """Map full body stamps to TOC suffix pieces per main target page."""
    if not targets or not full_labels:
        return {}
    digit_bases = [_toc_digit_base(t, toc_info, base_texts) for t in targets]
    suffixes: List[str] = []
    for i, full in enumerate(full_labels):
        body_idx = min(i, len(bases) - 1)
        digit_idx = min(i, len(digit_bases) - 1) if digit_bases else body_idx
        body = bases[body_idx] if bases else ""
        digit = digit_bases[digit_idx] if digit_bases else str(targets[min(i, len(targets) - 1)])
        suffixes.append(toc_suffix_from_full_label(body, full, digit))
    return per_page_toc_label_pieces_from_suffixes(targets, suffixes)


def resolve_swap_labels(
    targets: List[int],
    bases: List[str],
    source_page_count: int,
    *,
    letter: str,
    num_digits: int,
    custom_labels: Optional[List[str]] = None,
) -> List[str]:
    """Build full body-page stamps (auto or user-edited)."""
    n = max(1, int(source_page_count))
    full_bases = bases if bases else ["?"]
    auto = labels_for_multi_swap(
        full_bases, n, letter=letter, num_digits=num_digits
    )
    if not custom_labels:
        return auto
    cleaned = [str(label).strip() for label in custom_labels if str(label).strip()]
    if not cleaned:
        return auto
    result = list(cleaned)
    while len(result) < n:
        index = len(result)
        result.append(auto[index] if index < len(auto) else auto[-1])
    return result[:n]


def _toc_digit_base(
    page_number: int,
    toc_info: TocInfo,
    base_texts: Dict[int, str],
) -> str:
    """Prefer a TOC printed digit for ``page_number``; else trailing digits / page."""
    for entry in toc_info.entries:
        if entry.page_number != page_number:
            continue
        label = (entry.page_label or "").strip()
        wrapped = re.fullmatch(
            r"[\-\u2013]+\s*(\d+)\s*[\-\u2013]+",
            label,
        )
        if wrapped:
            return wrapped.group(1)
        if re.fullmatch(r"\d+", label):
            return label
    base = (base_texts.get(page_number) or "").strip()
    trailing = re.search(r"(\d+)\s*$", base)
    if trailing:
        return trailing.group(1)
    return str(page_number)


def _owning_toc_entry_index(page: int, entries: List[TocEntry]) -> Optional[int]:
    """
    Index of the TOC entry whose chapter spans ``page``.

    A chapter that starts at page 11 with the next TOC row at 15 owns pages
    11–14. Returns the latest entry with ``page_number <= page``.
    """
    best_idx: Optional[int] = None
    best_page = -1
    for index, entry in enumerate(entries):
        start = int(entry.page_number or 0)
        if start <= 0:
            continue
        if start <= page and start >= best_page:
            best_page = start
            best_idx = index
    return best_idx


def validate_swap_operations(
    operations: List[SwapOperation],
    main_page_count: int,
) -> None:
    if main_page_count < 1:
        raise ValueError("Main document has no pages")
    if not operations:
        return

    seen: set = set()
    for op in operations:
        try:
            targets = op.target_pages(main_page_count)
        except ValueError as exc:
            raise ValueError(f"Invalid main pages '{op.target_pages_spec}': {exc}") from exc
        if not targets:
            raise ValueError("Each swap must target at least one main page")
        # Require a contiguous block so replacement is unambiguous.
        expected = list(range(targets[0], targets[0] + len(targets)))
        if targets != expected:
            raise ValueError(
                f"Main pages '{op.target_pages_spec}' must be a contiguous range "
                f"(got {targets})"
            )
        for page in targets:
            if page in seen:
                raise ValueError(f"Page {page} is targeted by more than one swap")
            seen.add(page)
        if not op.source_path or not Path(op.source_path).is_file():
            raise ValueError(
                f"Missing swap source file for main pages {op.target_pages_spec}"
            )


def validate_add_page_insertions(
    insertions: List[InsertionSegment],
    main_page_count: int,
) -> None:
    """Validate Add Pages insertion segments against the main document."""
    if main_page_count < 1:
        raise ValueError("Main document has no pages")
    for seg in insertions:
        if not seg.file_path or not Path(seg.file_path).is_file():
            raise ValueError("Each Add Pages row needs a valid PDF source file")
        if seg.insert_after < 0 or seg.insert_after > main_page_count:
            raise ValueError(
                f"Insert location must be between 0 and {main_page_count} "
                f"(0 = beginning); got {seg.insert_after}"
            )


def toc_and_new_page_indices(
    toc_info: Optional[TocInfo],
    main_count: int,
    old_to_new: Dict[int, int],
    new_content_indices: List[int],
) -> List[int]:
    """
    0-based output page indices for updated TOC sheets plus swapped/added pages.

    TOC sheet indices are remapped through ``old_to_new`` when pages were
    inserted before the TOC. Duplicate indices are removed; order is ascending.
    """
    indices: set = set(int(i) for i in new_content_indices if i >= 0)
    if toc_info is not None and toc_info.toc_page_index >= 0:
        count = max(1, int(toc_info.toc_page_count or 1))
        for offset in range(count):
            old_idx = toc_info.toc_page_index + offset
            if 0 <= old_idx < main_count:
                indices.add(int(old_to_new.get(old_idx, old_idx)))
    return sorted(indices)


def write_page_subset(
    source_pdf_path: str,
    page_indices_0based: List[int],
    output_path: str,
) -> int:
    """Copy selected pages from ``source_pdf_path`` into ``output_path``. Returns page count."""
    from PyPDF2 import PdfReader, PdfWriter

    reader = PdfReader(source_pdf_path, strict=False)
    writer = PdfWriter()
    total = len(reader.pages)
    for index in page_indices_0based:
        if 0 <= index < total:
            writer.add_page(reader.pages[index])
    if not writer.pages:
        raise ValueError("No TOC or new pages to write")
    with open(output_path, "wb") as handle:
        writer.write(handle)
    return len(writer.pages)


def build_old_to_new_page_map(
    main_count: int,
    operations: List[SwapOperation],
    replacement_lengths: Dict[str, int],
    inserts_after_counts: Optional[Dict[int, int]] = None,
) -> Dict[int, int]:
    """
    Map original 0-based page index -> corresponding output page index.

    For a contiguous swap of M main pages with N replacement pages:
      - target[i] maps to the i-th replacement page when i < N
      - leftover targets (N < M) map to the last replacement page
      - extra replacement pages (N > M) have no original source page; TOC/bookmarks
        for the last target still land on that target's first replacement page
        (the lettered page), not the extras

    ``inserts_after_counts`` maps original 1-based page number (0 = beginning)
    to how many pages were inserted after that page.
    """
    op_by_first: Dict[int, SwapOperation] = {}
    op_targets: Dict[str, List[int]] = {}
    for op in operations:
        targets = op.target_pages(main_count)
        op_targets[op.id] = targets
        op_by_first[targets[0]] = op

    inserts = inserts_after_counts or {}
    mapping: Dict[int, int] = {}
    new_index = int(inserts.get(0, 0))
    old_index = 0
    while old_index < main_count:
        page_num = old_index + 1
        op = op_by_first.get(page_num)
        if op is None:
            mapping[old_index] = new_index
            new_index += 1
            new_index += int(inserts.get(page_num, 0))
            old_index += 1
            continue
        targets = op_targets[op.id]
        n = max(1, replacement_lengths.get(op.id, 1))
        for i, t in enumerate(targets):
            if i < n:
                mapping[t - 1] = new_index + i
            else:
                mapping[t - 1] = new_index + n - 1
        new_index += n
        for t in targets:
            new_index += int(inserts.get(t, 0))
        # targets are 1-based; last page number equals the 0-based index of
        # the page immediately after the replaced block.
        old_index = targets[-1]
    return mapping


# Backward-compatible alias
build_old_to_new_first_page = build_old_to_new_page_map


def position_key_from_name(position_name: str) -> str:
    """Map an Inserter preset name (e.g. ``Bottom Centre``) to a position key."""
    name = (position_name or "").strip()
    if name in POSITION_NAME_TO_KEY:
        return POSITION_NAME_TO_KEY[name]
    key = name.lower().replace(" ", "_")
    if key in PAGE_NUMBER_POSITIONS:
        return key
    return DEFAULT_PAGE_NUMBER_POSITION


def example_text_from_numbering(
    numbering: Dict[str, Any],
    sample_page: int = 1,
) -> str:
    """Build an example stamp string from an Inserter session ``numbering`` block."""
    use_label = bool(numbering.get("use_label", False))
    label_type = str(numbering.get("label_type", "page")).lower()
    if label_type == "seite":
        label = "Seite"
    elif label_type == "custom":
        label = str(numbering.get("custom_label", "") or "Page")
    else:
        label = "Page"
    return format_page_number_text(
        max(1, int(sample_page)),
        use_label=use_label,
        label=label,
        prefix=str(numbering.get("chapter_prefix", "")),
        num_digits=max(1, int(numbering.get("num_digits", 1))),
        separator=str(numbering.get("separator", DEFAULT_SEPARATOR) or DEFAULT_SEPARATOR),
        suffix=str(numbering.get("suffix", "")),
    )


def page_number_settings_from_session(numbering: Dict[str, Any]) -> PageNumberSettings:
    """Build stamp settings from an Inserter session ``numbering`` dict."""
    use_label = bool(numbering.get("use_label", False))
    label_type = str(numbering.get("label_type", "page")).lower()
    if label_type == "seite":
        label = "Seite"
    elif label_type == "custom":
        label = str(numbering.get("custom_label", "") or "Page")
    else:
        label = "Page"

    position_name = str(numbering.get("position_name", DEFAULT_POSITION) or DEFAULT_POSITION)
    position_mode = str(numbering.get("position_mode", POSITION_RELATIVE))
    if position_mode not in (POSITION_RELATIVE, POSITION_ABSOLUTE):
        position_mode = POSITION_RELATIVE

    origin = str(numbering.get("position_origin", DEFAULT_ORIGIN) or DEFAULT_ORIGIN)
    x_percent = float(numbering.get("x_percent", 50.0))
    y_percent = float(numbering.get("y_percent", 5.0))
    x_cm = float(numbering.get("x_cm", 0.0))
    y_cm = float(numbering.get("y_cm", 0.0))

    if position_name in POSITION_PRESETS and position_mode == POSITION_RELATIVE:
        preset = POSITION_PRESETS[position_name]
        # Prefer saved sliders; fall back to preset when missing.
        if "x_percent" not in numbering:
            x_percent = preset[0]
        if "y_percent" not in numbering:
            y_percent = preset[1]
        if "position_origin" not in numbering:
            origin = preset[3]
        anchor = preset[2]
    else:
        anchor = preset_anchor(position_name) if position_name in POSITION_PRESETS else "center"
        if x_percent < 35:
            anchor = "left"
        elif x_percent > 65:
            anchor = "right"
        else:
            anchor = "center"

    rgb = numbering.get("font_color_rgb", [0, 0, 0])
    try:
        color = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
    except (TypeError, ValueError, IndexError):
        color = (0, 0, 0)

    return PageNumberSettings(
        use_label=use_label,
        label_text=label,
        chapter_prefix=str(numbering.get("chapter_prefix", "")),
        num_digits=max(1, int(numbering.get("num_digits", 1))),
        separator=str(numbering.get("separator", DEFAULT_SEPARATOR) or DEFAULT_SEPARATOR),
        position_name=position_name,
        position_mode=position_mode,
        position_origin=origin,
        x_percent=x_percent,
        y_percent=y_percent,
        x_cm=x_cm,
        y_cm=y_cm,
        text_anchor=anchor,
        font_name=str(numbering.get("font_name", DEFAULT_FONT) or DEFAULT_FONT),
        font_size=float(numbering.get("font_size", DEFAULT_FONT_SIZE) or DEFAULT_FONT_SIZE),
        font_color_rgb=color,
        suffix=str(numbering.get("suffix", "")),
        use_white_background=bool(numbering.get("use_white_background", True)),
    )


def build_page_number_pattern(example: str) -> Optional[Pattern[str]]:
    """
    Build a regex that matches stamps shaped like ``example``.

    Digit runs become ``\\d+`` (variable width). Other characters are literals.
    Whitespace in the example becomes flexible ``\\s+``.
    """
    cleaned = (example or "").strip()
    if not cleaned or not re.search(r"\d", cleaned):
        return None

    parts: List[str] = []
    i = 0
    length = len(cleaned)
    while i < length:
        ch = cleaned[i]
        if ch.isspace():
            while i < length and cleaned[i].isspace():
                i += 1
            parts.append(r"\s+")
            continue
        if ch.isdigit():
            while i < length and cleaned[i].isdigit():
                i += 1
            parts.append(r"\d+")
            continue
        parts.append(re.escape(ch))
        i += 1

    try:
        return re.compile("".join(parts), re.IGNORECASE)
    except re.error:
        return None


def _region_bounds(
    position: str,
    width: float,
    height: float,
) -> Tuple[float, float, float, float]:
    """
    Return (x0, top, x1, bottom) in pdfplumber coordinates for a stamp region.

    Uses a tighter band (~15% of page height) aligned to preset corners.
    """
    key = (position or DEFAULT_PAGE_NUMBER_POSITION).strip().lower().replace(" ", "_")
    band = 0.15
    if key.startswith("top"):
        top, bottom = 0.0, height * band
    else:
        top, bottom = height * (1.0 - band), height

    if key.endswith("left"):
        x0, x1 = 0.0, width * 0.45
    elif key.endswith("right"):
        x0, x1 = width * 0.55, width
    else:
        x0, x1 = width * 0.15, width * 0.85
    return x0, top, x1, bottom


def _best_pattern_match(
    text: str,
    pattern: Pattern[str],
) -> Optional[str]:
    """Return the best (prefer full-line, else shortest) regex match in ``text``."""
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    full = pattern.fullmatch(cleaned)
    if full:
        return full.group(0).strip()
    matches = [m.group(0).strip() for m in pattern.finditer(cleaned) if m.group(0).strip()]
    if not matches:
        return None
    # Prefer compact stamp-like matches over long accidental spans.
    matches.sort(key=lambda s: (len(s), s))
    return matches[0]


def _extract_stamp_from_plain_text(
    pdf_path: str,
    page_index_0based: int,
    pattern: Pattern[str],
    last_n: int = 10,
) -> str:
    """
    Method A: match ``pattern`` against the last ``last_n`` nonempty plain-text lines.

    Uses PyPDF2 ``extract_text`` only — fast and reliable for footer stamps like
    ``Page 10A.9.01``.
    """
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        return ""

    try:
        reader = PdfReader(pdf_path, strict=False)
        if page_index_0based < 0 or page_index_0based >= len(reader.pages):
            return ""
        text = reader.pages[page_index_0based].extract_text() or ""
    except Exception:
        return ""

    nonempty = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not nonempty:
        return ""

    for line in reversed(nonempty[-last_n:]):
        hit = _best_pattern_match(line, pattern)
        if hit:
            return hit
    return ""


def _extract_stamp_from_grouped_lines(
    pdf_path: str,
    page_index_0based: int,
    pattern: Pattern[str],
) -> str:
    """Method B fallback: regex over pdfplumber grouped lines (prefer last hit)."""
    try:
        import pdfplumber
    except ImportError:
        return ""

    try:
        with pdfplumber.open(pdf_path) as pdf:
            if page_index_0based < 0 or page_index_0based >= len(pdf.pages):
                return ""
            page = pdf.pages[page_index_0based]
            words = page.extract_words() or []
            last_hit = ""
            for line in _group_words_into_lines(words):
                line_text = _line_text_from_words(line)
                hit = _best_pattern_match(line_text, pattern)
                if hit:
                    last_hit = hit
            return last_hit
    except Exception:
        return ""


def _line_text_from_words(words: List[dict]) -> str:
    return " ".join(
        w["text"] for w in sorted(words, key=lambda item: float(item["x0"]))
    ).strip()


def _word_span_matching_hit(line_words: List[dict], hit: str) -> Optional[List[dict]]:
    """Return the contiguous word span whose joined text equals ``hit``."""
    target = re.sub(r"\s+", " ", (hit or "").strip())
    if not target or not line_words:
        return None
    ordered = sorted(line_words, key=lambda item: float(item["x0"]))
    n = len(ordered)
    for i in range(n):
        for j in range(i, n):
            span = ordered[i : j + 1]
            joined = re.sub(
                r"\s+",
                " ",
                " ".join((w.get("text") or "").strip() for w in span),
            ).strip()
            if joined == target:
                return span
            compact = "".join((w.get("text") or "").strip() for w in span)
            if compact == target.replace(" ", ""):
                return span
    return None


@dataclass
class StampGeometry:
    """Detected body stamp location on a page (fractions are bottom-left origin)."""

    text: str
    x_frac: float
    y_frac: float
    text_anchor: str
    font_size: float
    page_width: float
    page_height: float


def locate_stamp_geometry(
    pdf_path: str,
    page_index_0based: int,
    stamp_text: str,
    *,
    example: str = "",
) -> Optional[StampGeometry]:
    """
    Locate the on-page bounding box of ``stamp_text`` via pdfplumber words.

    Returns fractions of page width/height (bottom-left) and a text anchor so
    Swapper can redraw at the same place on replacement pages.
    """
    hit = (stamp_text or "").strip()
    if not hit:
        return None
    pattern = build_page_number_pattern(example or hit)
    try:
        import pdfplumber
    except ImportError:
        return None

    try:
        with pdfplumber.open(pdf_path) as pdf:
            if page_index_0based < 0 or page_index_0based >= len(pdf.pages):
                return None
            page = pdf.pages[page_index_0based]
            width = float(page.width or 0.0)
            height = float(page.height or 0.0)
            if width <= 0 or height <= 0:
                return None
            words = page.extract_words(extra_attrs=["size"]) or []
            best_span: Optional[List[dict]] = None
            best_top = -1.0

            for line in _group_words_into_lines(words):
                line_text = _line_text_from_words(line)
                matched = _best_pattern_match(line_text, pattern) if pattern else None
                if matched != hit and hit not in line_text:
                    # Still try exact span for the known hit string.
                    if hit not in line_text and hit.replace(" ", "") not in line_text.replace(
                        " ", ""
                    ):
                        continue
                span = _word_span_matching_hit(line, hit)
                if span is None and matched:
                    span = _word_span_matching_hit(line, matched)
                if not span:
                    continue
                top = min(float(w.get("top", 0.0)) for w in span)
                # Prefer the lowest stamp on the page (footer).
                if top >= best_top:
                    best_top = top
                    best_span = span

            if not best_span:
                return None

            x0 = min(float(w["x0"]) for w in best_span)
            x1 = max(float(w["x1"]) for w in best_span)
            top = min(float(w["top"]) for w in best_span)
            bottom = max(float(w["bottom"]) for w in best_span)
            sizes = [
                float(w["size"])
                for w in best_span
                if w.get("size") is not None
            ]
            font_size = sizes[0] if sizes else DEFAULT_FONT_SIZE

            mid_x = (x0 + x1) / 2.0
            if mid_x >= width * 0.60:
                anchor = "right"
                x_pt = x1
            elif mid_x <= width * 0.40:
                anchor = "left"
                x_pt = x0
            else:
                anchor = "center"
                x_pt = mid_x

            # Baseline ≈ bottom edge of glyph box in bottom-left coords.
            y_pt = height - bottom

            return StampGeometry(
                text=hit,
                x_frac=max(0.0, min(1.0, x_pt / width)),
                y_frac=max(0.0, min(1.0, y_pt / height)),
                text_anchor=anchor,
                font_size=max(0.1, font_size),
                page_width=width,
                page_height=height,
            )
    except Exception:
        return None


def settings_from_stamp_geometry(
    geometry: StampGeometry,
    base: Optional[PageNumberSettings] = None,
) -> PageNumberSettings:
    """Build stamp settings that draw at the detected page location."""
    base = base or PageNumberSettings(use_white_background=True)
    return PageNumberSettings(
        use_label=base.use_label,
        label_text=base.label_text,
        chapter_prefix=base.chapter_prefix,
        num_digits=base.num_digits,
        separator=base.separator,
        position_name=base.position_name,
        position_mode=base.position_mode,
        position_origin=base.position_origin,
        x_percent=base.x_percent,
        y_percent=base.y_percent,
        x_cm=base.x_cm,
        y_cm=base.y_cm,
        text_anchor=geometry.text_anchor,
        font_name=base.font_name or DEFAULT_FONT,
        font_size=geometry.font_size or base.font_size,
        font_color_rgb=base.font_color_rgb,
        suffix=base.suffix,
        use_white_background=True,
        background_color_rgb=None,
        x_frac=geometry.x_frac,
        y_frac=geometry.y_frac,
    )


def with_stamp_font_color(
    settings: PageNumberSettings,
    rgb: Optional[Tuple[int, int, int]],
) -> PageNumberSettings:
    """Return settings that draw stamp text in ``rgb`` (optional white box kept)."""
    if rgb is None:
        return settings
    colour = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
    return PageNumberSettings(
        use_label=settings.use_label,
        label_text=settings.label_text,
        chapter_prefix=settings.chapter_prefix,
        num_digits=settings.num_digits,
        separator=settings.separator,
        position_name=settings.position_name,
        position_mode=settings.position_mode,
        position_origin=settings.position_origin,
        x_percent=settings.x_percent,
        y_percent=settings.y_percent,
        x_cm=settings.x_cm,
        y_cm=settings.y_cm,
        text_anchor=settings.text_anchor,
        font_name=settings.font_name,
        font_size=settings.font_size,
        font_color_rgb=colour,
        suffix=settings.suffix,
        use_white_background=True,
        background_color_rgb=None,
        x_frac=settings.x_frac,
        y_frac=settings.y_frac,
    )


def _find_stamp_in_words(
    words: List[dict],
    pattern: Pattern[str],
) -> Optional[str]:
    if not words:
        return None
    best: Optional[str] = None
    best_len = 10**9
    for line in _group_words_into_lines(words):
        line_text = _line_text_from_words(line)
        hit = _best_pattern_match(line_text, pattern)
        if hit and len(hit) < best_len:
            best = hit
            best_len = len(hit)
    return best


def extract_page_number_base_text(
    pdf_path: str,
    page_index_0based: int,
    *,
    example: str = "",
    expected_page: int = 0,
    position: str = DEFAULT_PAGE_NUMBER_POSITION,
) -> str:
    """
    Read the printed page-number stamp shaped like ``example``.

    Builds a regex from ``example`` and searches plain text, grouped lines, then
    the stamp region at ``position``. Returns the exact stamp text when found.
    """
    fallback = str(expected_page) if expected_page > 0 else ""
    pattern = build_page_number_pattern(example)
    if pattern is None:
        return fallback

    hit = _extract_stamp_from_plain_text(pdf_path, page_index_0based, pattern)
    if hit:
        return hit

    hit = _extract_stamp_from_grouped_lines(pdf_path, page_index_0based, pattern)
    if hit:
        return hit

    # Method C: geometry region search (last resort).
    try:
        import pdfplumber
    except ImportError:
        return fallback

    try:
        with pdfplumber.open(pdf_path) as pdf:
            if page_index_0based < 0 or page_index_0based >= len(pdf.pages):
                return fallback
            page = pdf.pages[page_index_0based]
            width = float(page.width or 0.0)
            height = float(page.height or 0.0)
            words = page.extract_words() or []
            if not words or width <= 0 or height <= 0:
                return fallback

            x0, top, x1, bottom = _region_bounds(position, width, height)
            region_words = [
                word
                for word in words
                if x0 <= float(word.get("x0", 0.0)) <= x1
                and top <= float(word.get("top", 0.0)) <= bottom
            ]
            found = _find_stamp_in_words(region_words, pattern)
            if found:
                return found

            key = (position or DEFAULT_PAGE_NUMBER_POSITION).lower()
            if key.startswith("top"):
                wider = [w for w in words if float(w.get("top", 0.0)) <= height * 0.28]
            else:
                wider = [w for w in words if float(w.get("top", 0.0)) >= height * 0.72]
            found = _find_stamp_in_words(wider, pattern)
            if found:
                return found

            found = _find_stamp_in_words(words, pattern)
            if found:
                return found
    except Exception:
        return fallback

    return fallback


def _default_stamp_settings(position: str = DEFAULT_PAGE_NUMBER_POSITION) -> PageNumberSettings:
    key = (position or DEFAULT_PAGE_NUMBER_POSITION).strip().lower().replace(" ", "_")
    preset_name = PAGE_NUMBER_POSITIONS.get(key, "Bottom Centre")

    x_percent, y_percent = 50.0, 5.0
    anchor = "center"
    origin = "bottom_left"
    if preset_name in POSITION_PRESETS:
        x_percent, y_percent, anchor, origin = (
            POSITION_PRESETS[preset_name][0],
            POSITION_PRESETS[preset_name][1],
            POSITION_PRESETS[preset_name][2],
            POSITION_PRESETS[preset_name][3],
        )
    return PageNumberSettings(
        use_label=False,
        num_digits=1,
        position_name=preset_name,
        position_mode=POSITION_RELATIVE,
        position_origin=origin,
        x_percent=x_percent,
        y_percent=y_percent,
        text_anchor=anchor or preset_anchor(preset_name),
        font_name=DEFAULT_FONT,
        font_size=DEFAULT_FONT_SIZE,
        use_white_background=True,
    )


def _extract_outline_via_pikepdf(pdf_path: str) -> List[TocEntry]:
    """Fallback outline extraction when PyPDF2 returns nothing usable."""
    try:
        import pikepdf
    except ImportError:
        return []

    entries: List[TocEntry] = []
    try:
        with pikepdf.Pdf.open(pdf_path) as pdf:
            # Indirect objects are not hashable — key by (objnum, generation).
            page_index = {
                pdf.pages[i].obj.objgen: i for i in range(len(pdf.pages))
            }

            def _page_from_dest(dest) -> int:
                if dest is None:
                    return 0
                page_obj = dest[0] if isinstance(dest, (list, pikepdf.Array)) else dest
                if page_obj is None:
                    return 0
                try:
                    key = page_obj.objgen
                except Exception:
                    try:
                        key = page_obj.obj.objgen
                    except Exception:
                        return 0
                idx = page_index.get(key)
                return (idx + 1) if idx is not None else 0

            def walk(items, level: int) -> None:
                for item in items:
                    title = str(getattr(item, "title", "") or "").strip()
                    page_number = 0
                    try:
                        page_number = _page_from_dest(item.destination)
                    except Exception:
                        page_number = 0
                    if title and page_number > 0:
                        entries.append(
                            TocEntry(
                                title=title,
                                page_number=page_number,
                                level=level,
                            )
                        )
                    children = list(getattr(item, "children", None) or [])
                    if children:
                        walk(children, level + 1)

            with pdf.open_outline() as outline:
                walk(list(outline.root), 1)
    except Exception:
        return []

    return entries


def _remap_bookmark_entries(
    source_entries: List[TocEntry],
    main_count: int,
    old_to_new: Dict[int, int],
) -> List[TocEntry]:
    remapped: List[TocEntry] = []
    for entry in source_entries:
        if entry.page_number < 1 or entry.page_number > main_count:
            continue
        old_index = entry.page_number - 1
        new_index = old_to_new.get(old_index, old_index)
        remapped.append(
            TocEntry(
                title=entry.title,
                page_number=new_index + 1,
                level=entry.level,
                page_label=entry.page_label,
            )
        )
    return apply_hierarchy_levels(remapped)


class PageSwapper:
    """Assemble a PDF by replacing selected main pages with pages from other PDFs."""

    def __init__(self, pdf_processor) -> None:
        self.pdf_processor = pdf_processor

    def process(
        self,
        main_pdf_path: str,
        operations: List[SwapOperation],
        output_path: Optional[str] = None,
        toc_info: Optional[TocInfo] = None,
        stamp_settings: Optional[PageNumberSettings] = None,
        page_number_position: str = DEFAULT_PAGE_NUMBER_POSITION,
        page_number_texts: Optional[Dict[int, str]] = None,
        example_text: str = "",
        swap_letter: str = "A",
        range_word: str = "to",
        num_digits: int = 1,
        toc_highlight_rgb: Optional[Tuple[int, int, int]] = None,
        colour_on_pages: bool = True,
        insertions: Optional[List[InsertionSegment]] = None,
        toc_new_output_path: Optional[str] = None,
    ) -> Dict[str, object]:
        """
        Build the swapped / extended PDF with remapped bookmarks and TOC labels.

        ``operations`` replace main pages (letter / -i stamps).
        ``insertions`` add pages after a main page (``{page}-1`` … stamps) without
        replacing anything; only the TOC line at the insert anchor is rewritten.

        ``colour_on_pages`` controls whether ``toc_highlight_rgb`` is also applied
        to swapped/added page stamps (default ``True``). TOC highlighting always
        uses ``toc_highlight_rgb`` when provided.

        Provide ``output_path`` for the full document and/or ``toc_new_output_path``
        for a PDF containing only the updated TOC sheets plus swapped/added pages.
        At least one path is required.
        """
        from collections import defaultdict

        from PyPDF2 import PdfReader, PdfWriter

        if not output_path and not toc_new_output_path:
            raise ValueError("Provide at least one output path")

        reader = PdfReader(main_pdf_path, strict=False)
        main_count = len(reader.pages)
        operations = list(operations or [])
        insertions = list(insertions or [])
        if not operations and not insertions:
            raise ValueError("Add at least one swap or Add Pages row")

        validate_swap_operations(operations, main_count)
        validate_add_page_insertions(insertions, main_count)

        letter = ""
        if operations:
            letter = normalize_swap_letter(swap_letter)
        digits = max(1, int(num_digits))
        connector = (range_word or "to").strip() or "to"
        highlight = toc_highlight_rgb

        settings = stamp_settings or _default_stamp_settings(page_number_position)
        stamp_colour = highlight if colour_on_pages else None
        settings = with_stamp_font_color(settings, stamp_colour)
        temp_dir = self.pdf_processor._create_temp_dir()

        op_targets: Dict[str, List[int]] = {
            op.id: op.target_pages(main_count) for op in operations
        }
        op_by_first: Dict[int, SwapOperation] = {
            targets[0]: op
            for op, targets in ((op, op_targets[op.id]) for op in operations)
        }
        all_target_pages = {p for targets in op_targets.values() for p in targets}

        source_page_lists: Dict[str, List[int]] = {}
        replacement_lengths: Dict[str, int] = {}
        for op in operations:
            src_reader = PdfReader(op.source_path, strict=False)
            pages = parse_pages_spec(op.source_pages_spec, len(src_reader.pages))
            if not pages:
                raise ValueError(
                    f"No pages selected from {Path(op.source_path).name}"
                )
            source_page_lists[op.id] = pages
            replacement_lengths[op.id] = len(pages)

        # Resolve insertion page lists and group by insert_after.
        insert_page_lists: Dict[str, List[int]] = {}
        inserts_by_after: Dict[int, List[InsertionSegment]] = defaultdict(list)
        inserts_after_counts: Dict[int, int] = defaultdict(int)
        for seg in insertions:
            src_reader = PdfReader(seg.file_path, strict=False)
            pages = parse_pages_spec(seg.pages_spec, len(src_reader.pages))
            if not pages:
                raise ValueError(
                    f"No pages selected from {Path(seg.file_path).name}"
                )
            insert_page_lists[seg.id] = pages
            inserts_by_after[seg.insert_after].append(seg)
            inserts_after_counts[seg.insert_after] += len(pages)

        provided = page_number_texts or {}
        base_texts: Dict[int, str] = {}
        stamp_geometries: Dict[int, StampGeometry] = {}

        pages_needing_bases = set(all_target_pages)
        pages_needing_bases.update(
            after for after in inserts_after_counts if after >= 1
        )
        for page_num in sorted(pages_needing_bases):
            if page_num in provided and provided[page_num].strip():
                base_texts[page_num] = provided[page_num].strip()
            else:
                base_texts[page_num] = extract_page_number_base_text(
                    main_pdf_path,
                    page_num - 1,
                    example=example_text,
                    expected_page=page_num,
                    position=page_number_position,
                )
            geo = locate_stamp_geometry(
                main_pdf_path,
                page_num - 1,
                base_texts[page_num],
                example=example_text,
            )
            if geo is not None:
                stamp_geometries[page_num] = geo

        old_to_new = build_old_to_new_page_map(
            main_count,
            operations,
            replacement_lengths,
            inserts_after_counts=dict(inserts_after_counts),
        )

        writer = PdfWriter()
        stamps: Dict[int, str] = {}
        stamp_page_sources: Dict[int, int] = {}
        insertion_counts: List[Tuple[int, int]] = []
        summary_labels: List[Tuple[str, List[str]]] = []
        summary_inserts: List[Tuple[int, List[str]]] = []
        new_content_indices: List[int] = []

        def emit_inserts_after(after_page: int) -> None:
            segs = inserts_by_after.get(after_page, [])
            if not segs:
                return
            if after_page >= 1:
                base = base_texts.get(after_page) or str(after_page)
                geo_page = after_page
            else:
                base = "0"
                geo_page = 0
            suffix_index = 0
            combined_labels: List[str] = []
            total_added = 0
            for seg in segs:
                src_pages = insert_page_lists[seg.id]
                extract_path = os.path.join(temp_dir, f"add_{seg.id}.pdf")
                self.pdf_processor.extract_pdf_pages(
                    seg.file_path, src_pages, extract_path
                )
                extract_reader = PdfReader(extract_path, strict=False)
                n = len(extract_reader.pages)
                labels = [
                    f"{base}-{str(suffix_index + i + 1).zfill(digits)}"
                    for i in range(n)
                ]
                for i, src_page in enumerate(extract_reader.pages):
                    out_index = len(writer.pages)
                    writer.add_page(src_page)
                    stamps[out_index] = labels[i]
                    new_content_indices.append(out_index)
                    if geo_page >= 1:
                        stamp_page_sources[out_index] = geo_page
                combined_labels.extend(labels)
                suffix_index += n
                total_added += n
            if total_added:
                summary_inserts.append((after_page, combined_labels))
                insertion_counts.append((after_page, total_added))

        emit_inserts_after(0)

        page_index = 0
        while page_index < main_count:
            page_num = page_index + 1
            op = op_by_first.get(page_num)
            if op is None:
                writer.add_page(reader.pages[page_index])
                emit_inserts_after(page_num)
                page_index += 1
                continue

            targets = op_targets[op.id]
            src_pages = source_page_lists[op.id]
            extract_path = os.path.join(temp_dir, f"swap_{op.id}.pdf")
            self.pdf_processor.extract_pdf_pages(
                op.source_path, src_pages, extract_path
            )
            extract_reader = PdfReader(extract_path, strict=False)
            bases = [base_texts[t] for t in targets]
            labels = resolve_swap_labels(
                targets,
                bases,
                len(extract_reader.pages),
                letter=letter,
                num_digits=digits,
                custom_labels=op.custom_labels or None,
            )
            for i, src_page in enumerate(extract_reader.pages):
                out_index = len(writer.pages)
                writer.add_page(src_page)
                stamps[out_index] = labels[i]
                new_content_indices.append(out_index)
                geo_page = targets[i] if i < len(targets) else targets[-1]
                stamp_page_sources[out_index] = geo_page
            summary_labels.append((op.target_pages_spec, labels))
            n = len(src_pages)
            m = len(targets)
            if n != m:
                insertion_counts.append((targets[0], n - m))
            for t in targets:
                emit_inserts_after(t)
            page_index = targets[-1]

        merged_path = os.path.join(temp_dir, "swapped_merged.pdf")
        with open(merged_path, "wb") as handle:
            writer.write(handle)

        working_path = merged_path
        if toc_info is not None and toc_info.can_update_in_place:
            label_overrides, label_colors, label_color_suffixes = self._toc_label_overrides(
                toc_info,
                operations,
                op_targets,
                replacement_lengths,
                base_texts,
                letter=letter,
                range_word=connector,
                num_digits=digits,
                highlight_rgb=highlight,
                inserts_after_counts=dict(inserts_after_counts),
            )
            toc_updated = os.path.join(temp_dir, "swapped_toc.pdf")
            try:
                if update_toc_page_in_place(
                    merged_path,
                    toc_info,
                    insertion_counts,
                    toc_updated,
                    temp_dir,
                    label_overrides=label_overrides,
                    label_colors=label_colors,
                    label_color_suffixes=label_color_suffixes,
                ):
                    working_path = toc_updated
            except Exception:
                working_path = merged_path

        per_page_settings: Dict[int, PageNumberSettings] = {}
        for out_index, main_page in stamp_page_sources.items():
            geo = stamp_geometries.get(main_page)
            if geo is not None:
                per_page_settings[out_index] = settings_from_stamp_geometry(
                    geo, settings
                )
            else:
                per_page_settings[out_index] = settings

        stamped_path = os.path.join(temp_dir, "swapped_stamped.pdf")
        self._stamp_labels(
            working_path, stamped_path, stamps, settings, per_page_settings
        )

        # PyPDF2 assembly leaves Link /Dest refs pointing at removed pages.
        # Rewrite them from the source destinations through old_to_new.
        links_path = os.path.join(temp_dir, "swapped_links.pdf")
        working_for_bookmarks = stamped_path
        try:
            if remap_link_destinations(
                main_pdf_path,
                stamped_path,
                old_to_new,
                links_path,
                replaced_page_indices=set(page - 1 for page in all_target_pages),
            ):
                working_for_bookmarks = links_path
        except Exception:
            working_for_bookmarks = stamped_path

        full_output_path = output_path or os.path.join(temp_dir, "swapped_full.pdf")
        bookmark_entries = self._bookmark_entries_for_output(
            main_pdf_path, toc_info, main_count, old_to_new
        )
        wrote_bookmarks = False
        if bookmark_entries:
            try:
                wrote_bookmarks = bool(
                    apply_toc_bookmarks(
                        working_for_bookmarks, bookmark_entries, full_output_path
                    )
                )
            except Exception:
                wrote_bookmarks = False
        if not wrote_bookmarks:
            shutil.copy2(working_for_bookmarks, full_output_path)

        subset_indices = toc_and_new_page_indices(
            toc_info, main_count, old_to_new, new_content_indices
        )
        toc_new_pages = 0
        if toc_new_output_path:
            toc_new_pages = write_page_subset(
                full_output_path, subset_indices, toc_new_output_path
            )

        net_extra = sum(
            replacement_lengths[op.id] - len(op_targets[op.id]) for op in operations
        ) + sum(inserts_after_counts.values())
        return {
            "main_pages": main_count,
            "output_pages": main_count + net_extra,
            "swaps": summary_labels,
            "inserts": summary_inserts,
            "bases": base_texts,
            "entire_path": output_path,
            "toc_new_path": toc_new_output_path,
            "toc_new_pages": toc_new_pages,
            "toc_new_page_indices": subset_indices,
        }

    def _toc_label_overrides(
        self,
        toc_info: TocInfo,
        operations: List[SwapOperation],
        op_targets: Dict[str, List[int]],
        replacement_lengths: Dict[str, int],
        base_texts: Dict[int, str],
        *,
        letter: str,
        range_word: str,
        num_digits: int,
        highlight_rgb: Optional[Tuple[int, int, int]] = None,
        inserts_after_counts: Optional[Dict[int, int]] = None,
    ) -> Tuple[Dict[int, str], Dict[int, Tuple[int, int, int]], Dict[int, str]]:
        """
        TOC labels for swapped targets and Add Pages anchors.

        Each swapped TOC line keeps its own page mapping (``26`` → ``26C``).
        Only when extras are appended onto the last replaced page does that
        line become a short range (``30C bis 30-1``).

        When swapped pages are not listed in the TOC (e.g. swap 12–14 while
        chapters start at 11 and 15), their labels are appended onto the
        preceding chapter line: ``- 11 - 12a bis 14a``. Only the appended
        swap labels use the highlight colour; the original chapter page
        number stays black.

        Add Pages only rewrites the TOC line at the insert-after page
        (``30`` → ``30 to 30-3``) using the picked font colour; other lines keep
        their original printed labels.

        Returns:
            ``(label_overrides, label_colors, label_color_suffixes)`` where
            ``label_color_suffixes`` maps entry index -> trailing text that
            alone should be drawn in ``label_colors`` (prefix stays black).
        """
        overrides: Dict[int, str] = {}
        colors: Dict[int, Tuple[int, int, int]] = {}
        color_suffixes: Dict[int, str] = {}
        swapped_pages = {p for targets in op_targets.values() for p in targets}
        insert_pages = {
            page: count
            for page, count in (inserts_after_counts or {}).items()
            if page >= 1 and count > 0
        }
        toc_pages = {
            entry.page_number
            for entry in toc_info.entries
            if entry.page_number > 0
        }

        page_to_pieces: Dict[int, List[str]] = {}
        # TOC entry index -> label pieces for swapped pages that fall inside
        # that chapter but are not themselves TOC rows.
        owner_orphan_pieces: Dict[int, List[str]] = {}
        for op in operations:
            targets = op_targets[op.id]
            n = max(1, replacement_lengths.get(op.id, 1))
            bases = [base_texts[t] for t in targets]
            full_labels = resolve_swap_labels(
                targets,
                bases,
                n,
                letter=letter,
                num_digits=num_digits,
                custom_labels=op.custom_labels or None,
            )
            pieces_map = per_page_toc_label_pieces_from_full_labels(
                targets,
                full_labels,
                bases,
                toc_info,
                base_texts,
            )
            page_to_pieces.update(pieces_map)
            for page in targets:
                if page in toc_pages:
                    continue
                owner = _owning_toc_entry_index(page, toc_info.entries)
                if owner is None:
                    continue
                owner_orphan_pieces.setdefault(owner, []).extend(
                    pieces_map.get(page, [])
                )

        font_rgb = None
        if highlight_rgb is not None:
            font_rgb = (
                int(highlight_rgb[0]),
                int(highlight_rgb[1]),
                int(highlight_rgb[2]),
            )

        for index, entry in enumerate(toc_info.entries):
            toc_label = (entry.page_label or "").strip()
            wrapped = re.fullmatch(
                r"([\-\u2013]+)\s*(\d+)\s*([\-\u2013]+)",
                toc_label,
            )
            label: Optional[str] = None

            if entry.page_number in swapped_pages:
                pieces = page_to_pieces.get(entry.page_number)
                if not pieces:
                    base = _toc_digit_base(entry.page_number, toc_info, base_texts)
                    pieces = [
                        swap_label(base, 0, letter=letter, num_digits=num_digits)
                    ]

                if wrapped:
                    label = format_swap_range_label(
                        pieces,
                        range_word,
                        wrap_prefix=wrapped.group(1),
                        wrap_suffix=wrapped.group(3),
                    )
                else:
                    label = format_swap_range_label(pieces, range_word)

            elif entry.page_number in insert_pages:
                count = insert_pages[entry.page_number]
                digit_base = _toc_digit_base(
                    entry.page_number, toc_info, base_texts
                )
                if wrapped:
                    label = format_add_pages_toc_label(
                        digit_base,
                        count,
                        range_word=range_word,
                        num_digits=num_digits,
                        wrap_prefix=wrapped.group(1),
                        wrap_suffix=wrapped.group(3),
                    )
                else:
                    label = format_add_pages_toc_label(
                        digit_base,
                        count,
                        range_word=range_word,
                        num_digits=num_digits,
                    )

            orphan_pieces = owner_orphan_pieces.get(index, [])
            color_suffix: Optional[str] = None
            if orphan_pieces:
                append_text = format_swap_range_label(orphan_pieces, range_word)
                if label is None:
                    # Original chapter page kept as-is; only the appended swap
                    # labels (e.g. ``13b to 15-02``) use the highlight colour.
                    label = entry.display_page_label
                    color_suffix = append_text
                label = f"{(label or '').strip()} {append_text}".strip()

            if label is not None:
                overrides[index] = label
                if font_rgb is not None and (
                    entry.page_number in swapped_pages
                    or entry.page_number in insert_pages
                    or orphan_pieces
                ):
                    colors[index] = font_rgb
                    if color_suffix:
                        color_suffixes[index] = color_suffix
            else:
                # Keep original printed TOC numbers (do not shift later chapters).
                overrides[index] = entry.display_page_label

        return overrides, colors, color_suffixes

    def _bookmark_entries_for_output(
        self,
        main_pdf_path: str,
        toc_info: Optional[TocInfo],
        main_count: int,
        old_to_new: Dict[int, int],
    ) -> List[TocEntry]:
        """
        Remap the existing PDF outline after swaps.

        Prefer PyPDF2 outline extraction (reliable destinations). Fall back to
        pikepdf, then TOC entries only when no outline exists.
        """
        from PyPDF2 import PdfReader

        reader = PdfReader(main_pdf_path, strict=False)
        source_entries: List[TocEntry] = _extract_outline_entries(reader)
        if not source_entries:
            source_entries = _extract_outline_via_pikepdf(main_pdf_path)
        if not source_entries and toc_info is not None and toc_info.entries:
            source_entries = list(toc_info.entries)

        return _remap_bookmark_entries(source_entries, main_count, old_to_new)

    def _stamp_labels(
        self,
        pdf_path: str,
        output_path: str,
        stamps: Dict[int, str],
        default_settings: PageNumberSettings,
        per_page_settings: Optional[Dict[int, PageNumberSettings]] = None,
    ) -> None:
        if not stamps:
            shutil.copy2(pdf_path, output_path)
            return

        try:
            import pikepdf
        except ImportError as exc:
            raise ImportError(
                "pikepdf is required for the Swapper. Install it with: pip install pikepdf"
            ) from exc
        from reportlab.pdfgen import canvas

        proc = self.pdf_processor
        page_settings = per_page_settings or {}
        with pikepdf.Pdf.open(pdf_path) as pdf:
            for page_index, label in stamps.items():
                if page_index < 0 or page_index >= len(pdf.pages):
                    continue
                page = pdf.pages[page_index]
                mediabox = page.mediabox
                width = float(mediabox[2] - mediabox[0])
                height = float(mediabox[3] - mediabox[1])
                rotation = int(page.get("/Rotate", 0) or 0) % 360
                settings = page_settings.get(page_index, default_settings)

                fd, temp_stamp = tempfile.mkstemp(suffix=".pdf")
                os.close(fd)
                try:
                    c = canvas.Canvas(temp_stamp, pagesize=(width, height))
                    proc._draw_page_number_on_canvas(
                        c, label, width, height, settings, rotation
                    )
                    c.save()
                    with pikepdf.Pdf.open(temp_stamp) as stamp_pdf:
                        page.add_overlay(
                            stamp_pdf.pages[0],
                            shrink=False,
                            expand=False,
                        )
                finally:
                    if os.path.exists(temp_stamp):
                        os.remove(temp_stamp)
            pdf.save(output_path)
