"""
Save and load application sessions as JSON files in the user's home folder.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from getpass import getuser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SESSION_FOLDER_NAME = "The Reportinator"
# v2: flat inserter-only payload. v3: separate ``inserter`` / ``swapper`` tabs.
SESSION_VERSION = 3


def session_directory() -> Path:
    """Return ~/The Reportinator, creating it if needed."""
    folder = Path.home() / SESSION_FOLDER_NAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def windows_username() -> str:
    """Prefer USERNAME on Windows; fall back to getuser()."""
    name = os.environ.get("USERNAME") or os.environ.get("USER") or getuser()
    return re.sub(r'[<>:"/\\|?*\s]+', "_", name.strip()) or "user"


def sanitize_filename_part(text: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\s]+', "_", text.strip())
    return cleaned[:80] if cleaned else "document"


def build_session_filename(main_file_path: Optional[str], username: Optional[str] = None) -> str:
    """
    Build YYMMDD_username_mainfilename.json
    """
    date_part = datetime.now().strftime("%y%m%d")
    user_part = sanitize_filename_part(username or windows_username())
    if main_file_path:
        main_part = sanitize_filename_part(Path(main_file_path).stem)
    else:
        main_part = "session"
    return f"{date_part}_{user_part}_{main_part}.json"


def list_session_files() -> List[Tuple[Path, str]]:
    """
    List JSON session files in the session directory.

    Returns:
        List of (path, display summary) sorted newest first.
    """
    folder = session_directory()
    entries: List[Tuple[Path, str]] = []
    for path in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            mtime = "unknown"
        entries.append((path, f"{path.name}  (saved {mtime})"))
    return entries


def save_session(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": SESSION_VERSION, **data}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def load_session(path: Path) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("Session file is not a valid JSON object")
    return data


def inserter_section(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return the Insert-tab payload.

    Supports v3 ``inserter`` blocks and legacy flat ``files`` / ``numbering``.
    """
    block = data.get("inserter")
    if isinstance(block, dict):
        return block
    return {
        "files": data.get("files", {}) if isinstance(data.get("files"), dict) else {},
        "numbering": (
            data.get("numbering", {}) if isinstance(data.get("numbering"), dict) else {}
        ),
    }


def swapper_section(data: Dict[str, Any]) -> Dict[str, Any]:
    """Return the Swap-tab payload (empty dict when absent / legacy-only)."""
    block = data.get("swapper")
    return block if isinstance(block, dict) else {}


def numbering_from_session(data: Dict[str, Any]) -> Dict[str, Any]:
    """Numbering settings from a session (inserter section or legacy root)."""
    numbering = inserter_section(data).get("numbering")
    return numbering if isinstance(numbering, dict) else {}


def primary_action_button_style(lime_hex: str) -> str:
    """Shared big lime-green style for Save PDF / Save Session buttons."""
    return f"""
        QPushButton {{
            background-color: {lime_hex};
            color: #000000;
            font-weight: bold;
            font-size: 14px;
            border: 2px solid {lime_hex};
            border-radius: 5px;
            padding: 10px;
        }}
        QPushButton:hover {{
            background-color: #B8C800;
            border-color: #B8C800;
        }}
        QPushButton:disabled {{
            background-color: #E0E0E0;
            color: #808080;
            border-color: #E0E0E0;
        }}
    """
