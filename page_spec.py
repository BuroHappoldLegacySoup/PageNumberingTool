"""
Page-range specs and insertion segments for split PDF inserts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import uuid
from typing import List, Sequence


_RANGE_RE = re.compile(r"^(\d+)\s*-\s*(\d+)$")
_SINGLE_RE = re.compile(r"^\d+$")


@dataclass
class InsertionSegment:
    """One insert operation: optional page subset of a source file."""

    file_path: str
    pages_spec: str = ""  # empty = all pages; e.g. "1-4" or "5,6,7"
    insert_after: int = 0
    use_custom_insert: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "file_path": self.file_path,
            "pages_spec": self.pages_spec,
            "insert_after": self.insert_after,
            "use_custom_insert": self.use_custom_insert,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "InsertionSegment":
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex),
            file_path=str(data.get("file_path", "")),
            pages_spec=str(data.get("pages_spec", "")),
            insert_after=int(data.get("insert_after", 0)),
            use_custom_insert=bool(data.get("use_custom_insert", False)),
        )


def parse_pages_spec(spec: str, page_count: int) -> List[int]:
    """
    Parse a pages spec into ordered 1-based page numbers.

    Supports empty (all pages), ranges ``A-B``, and lists ``A,B,C``.
    """
    if page_count < 1:
        raise ValueError("Document has no pages")

    text = (spec or "").strip()
    if not text:
        return list(range(1, page_count + 1))

    pages: List[int] = []
    seen = set()
    for raw_token in text.split(","):
        token = raw_token.strip()
        if not token:
            continue
        range_match = _RANGE_RE.match(token)
        if range_match:
            start = int(range_match.group(1))
            end = int(range_match.group(2))
            if start > end:
                start, end = end, start
            if start < 1 or end > page_count:
                raise ValueError(
                    f"Page range {start}-{end} is outside 1–{page_count}"
                )
            for page in range(start, end + 1):
                if page not in seen:
                    pages.append(page)
                    seen.add(page)
            continue
        if _SINGLE_RE.match(token):
            page = int(token)
            if page < 1 or page > page_count:
                raise ValueError(f"Page {page} is outside 1–{page_count}")
            if page not in seen:
                pages.append(page)
                seen.add(page)
            continue
        raise ValueError(
            f"Invalid page token “{token}”. Use ranges like 1-4 or lists like 5,6,7."
        )

    if not pages:
        raise ValueError("No pages selected")
    return pages


def parse_split_groups(text: str, page_count: int) -> List[str]:
    """
    Parse a multi-line split definition into normalized pages_spec strings.

    Each non-empty line is one insertion group. Validates against page_count.
    Rejects overlapping pages across groups.
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        raise ValueError("Enter at least one page group (one per line).")

    groups: List[str] = []
    used = set()
    for line in lines:
        pages = parse_pages_spec(line, page_count)
        overlap = used.intersection(pages)
        if overlap:
            overlap_list = ", ".join(str(p) for p in sorted(overlap))
            raise ValueError(f"Page(s) {overlap_list} appear in more than one group.")
        used.update(pages)
        groups.append(_normalize_spec(pages))
    return groups


def _normalize_spec(pages: Sequence[int]) -> str:
    """Compress consecutive pages into ranges where possible."""
    if not pages:
        return ""
    ordered = list(pages)
    parts: List[str] = []
    start = ordered[0]
    prev = ordered[0]
    for page in ordered[1:]:
        if page == prev + 1:
            prev = page
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = page
    parts.append(str(start) if start == prev else f"{start}-{prev}")
    return ",".join(parts)


def format_pages_display(spec: str, page_count: int) -> str:
    """Human-readable pages cell text."""
    text = (spec or "").strip()
    if not text:
        if page_count <= 0:
            return "0"
        if page_count == 1:
            return "1"
        return f"1-{page_count}"
    return text


def count_pages_in_spec(spec: str, page_count: int) -> int:
    try:
        return len(parse_pages_spec(spec, page_count))
    except ValueError:
        return 0
