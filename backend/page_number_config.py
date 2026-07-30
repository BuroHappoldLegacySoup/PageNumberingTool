"""
Page numbering configuration: presets, formatting, and font list loading.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CONFIG_DIR = Path(__file__).resolve().parent.parent
FONTS_CONFIG_PATH = CONFIG_DIR / "fonts.json"

CUSTOM_POSITION = "Custom"

# Coordinate origin for X/Y offsets (margins measured inward from this corner).
ORIGIN_BOTTOM_LEFT = "bottom_left"
ORIGIN_BOTTOM_RIGHT = "bottom_right"
ORIGIN_TOP_LEFT = "top_left"
ORIGIN_TOP_RIGHT = "top_right"

# x%, y% from the preset's origin corner; text anchor; origin corner
POSITION_PRESETS: Dict[str, Tuple[float, float, str, str]] = {
    "Bottom Centre": (50.0, 5.0, "center", ORIGIN_BOTTOM_LEFT),
    "Bottom Left": (10.0, 5.0, "left", ORIGIN_BOTTOM_LEFT),
    "Bottom Right": (10.0, 5.0, "right", ORIGIN_BOTTOM_RIGHT),
    "Top Left": (10.0, 5.0, "left", ORIGIN_TOP_LEFT),
    "Top Centre": (50.0, 5.0, "center", ORIGIN_TOP_LEFT),
    "Top Right": (10.0, 5.0, "right", ORIGIN_TOP_RIGHT),
}

DEFAULT_POSITION = "Bottom Centre"
DEFAULT_ORIGIN = ORIGIN_BOTTOM_LEFT
DEFAULT_FONT = "Arial"
DEFAULT_FONT_SIZE = 8.0
DEFAULT_SEPARATOR = "."
POSITION_RELATIVE = "relative"
POSITION_ABSOLUTE = "absolute"
CM_TO_POINTS = 72.0 / 2.54


@dataclass
class PageNumberSettings:
    """Settings used when rendering page numbers on PDF output."""

    use_label: bool = False
    label_text: str = "Page"
    chapter_prefix: str = ""
    num_digits: int = 1
    separator: str = DEFAULT_SEPARATOR
    position_name: str = DEFAULT_POSITION
    position_mode: str = POSITION_RELATIVE
    position_origin: str = DEFAULT_ORIGIN
    x_percent: float = 50.0
    y_percent: float = 5.0
    x_cm: float = 0.0
    y_cm: float = 0.0
    text_anchor: str = "center"
    font_name: str = DEFAULT_FONT
    font_size: float = DEFAULT_FONT_SIZE
    font_color_rgb: Tuple[int, int, int] = (0, 0, 0)
    suffix: str = ""
    use_white_background: bool = False

    def format_number(self, page_num: int) -> str:
        return format_page_number_text(
            page_num,
            use_label=self.use_label,
            label=self.label_text,
            prefix=self.chapter_prefix,
            num_digits=self.num_digits,
            separator=self.separator,
            suffix=self.suffix,
        )


def load_font_names() -> List[str]:
    """Load font names from fonts.json; fall back to a built-in list."""
    try:
        with open(FONTS_CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        fonts = data.get("fonts", [])
        if fonts:
            return list(fonts)
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return [
        "Arial",
        "Helvetica",
        "Segoe UI",
        "Times New Roman",
        "Verdana",
    ]


def minimum_digits_for_page_count(total_pages: int) -> int:
    """
    Minimum zero-pad width from document length.

    Fewer than 10 pages allows 1 digit; 10+ requires at least 2; 100+ at least 3, etc.
    """
    if total_pages <= 0:
        return 1
    if total_pages < 10:
        return 1
    return len(str(total_pages))


def append_page_number_suffix(text: str, suffix: str) -> str:
    """Append suffix after the formatted page number (adds a space if needed)."""
    if not suffix:
        return text
    if suffix[:1].isspace():
        return text + suffix
    return f"{text} {suffix}"


def format_page_number_text(
    page_num: int,
    *,
    use_label: bool,
    label: str,
    prefix: str,
    num_digits: int,
    separator: str = DEFAULT_SEPARATOR,
    suffix: str = "",
) -> str:
    """
    Build display text for a page number.

    Example: Seite + prefix 5.7 + sep '.' + 3 digits -> "Seite 5.7.001"
    With suffix '-' -> "Seite 5.7.001 -"
    """
    padded = str(page_num).zfill(max(1, num_digits))
    prefix = prefix.strip()
    sep = separator if separator else DEFAULT_SEPARATOR
    if use_label and prefix:
        core = f"{label} {prefix}{sep}{padded}"
    elif use_label:
        core = f"{label} {padded}"
    elif prefix:
        core = f"{prefix}{sep}{padded}"
    else:
        core = padded
    return append_page_number_suffix(core, suffix)


def preset_anchor(name: str) -> str:
    if name in POSITION_PRESETS:
        return POSITION_PRESETS[name][2]
    return "center"


def origin_offsets_to_bottom_left(
    offset_x: float,
    offset_y: float,
    width: float,
    height: float,
    origin: str,
) -> Tuple[float, float]:
    """
    Convert offsets measured inward from ``origin`` into bottom-left viewer coords.

    X increases away from a left/right origin toward the opposite side.
    Y increases away from a bottom/top origin toward the opposite side.
    """
    if origin == ORIGIN_BOTTOM_RIGHT:
        return width - offset_x, offset_y
    if origin == ORIGIN_TOP_LEFT:
        return offset_x, height - offset_y
    if origin == ORIGIN_TOP_RIGHT:
        return width - offset_x, height - offset_y
    return offset_x, offset_y


def origin_percent_to_bottom_left_percent(
    x_percent: float,
    y_percent: float,
    origin: str,
) -> Tuple[float, float]:
    """Map origin-relative percentages to bottom-left percentages for previews."""
    if origin == ORIGIN_BOTTOM_RIGHT:
        return 100.0 - x_percent, y_percent
    if origin == ORIGIN_TOP_LEFT:
        return x_percent, 100.0 - y_percent
    if origin == ORIGIN_TOP_RIGHT:
        return 100.0 - x_percent, 100.0 - y_percent
    return x_percent, y_percent
