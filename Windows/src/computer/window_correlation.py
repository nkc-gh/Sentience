"""Shared matching of recorded foreground windows to tracked file revisions."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def matching_file_window_sessions(file_path: str, window_sessions: list[Any]) -> list[Any]:
    """Select any recorded window whose title explicitly names the file.

    The foreground-window recorder does not need to identify every possible
    editor. Windows applications, custom tools, browser-based editors, and
    terminal-hosted editors can all expose the tracked filename in their
    title, so the title match is the source of truth.
    """
    filename = Path(file_path).name.casefold()
    if not filename:
        return []
    return [
        session for session in window_sessions
        if filename in str(getattr(session, "window_title", "") or "").casefold()
    ]


def _is_editor_session(session: Any) -> bool:
    app = str(getattr(session, "app_name", "") or "").strip(" '\"").casefold()
    title = str(getattr(session, "window_title", "") or "").casefold()
    app_key = "".join(char for char in app if char.isalnum())
    editor_markers = (
        "editor", "ide", "vscode", "codium", "sublime", "kate", "pycharm",
        "intellij", "webstorm", "neovim", "nvim", "vim", "emacs", "zed", "cursor", "antigravity",
        # Native Windows editors commonly report executable names rather than
        # a generic "editor" label (for example, Notepad.exe).
        "notepad", "wordpad", "notepadplusplus", "devenv",
    )
    if any(marker in app_key for marker in editor_markers):
        return True
    if "code" == app_key or app_key.endswith(".code") or app_key.endswith("code"):
        return True
    # For terminal emulators, the title must show a known editor command as
    # well as the file name (checked by the caller).
    # Window titles from the current logger may retain quote delimiters from
    # the desktop command output, so accept any non-word boundary before the
    # editor command (for example, '"nano 1.c').
    return bool(re.search(r"(?:^|[^a-z0-9])(?:nano|nvim|vim|vi|emacs|micro|helix|hx|kak|ne)\s+", title))


def match_file_editor_sessions(
    file_path: str,
    event_time: datetime,
    window_sessions: list[Any],
    previous_event_time: datetime | None = None,
) -> dict[str, Any] | None:
    """Return observed foreground-window overlap when its title names the file."""
    event_time = event_time.replace(tzinfo=None)
    previous_event_time = previous_event_time.replace(tzinfo=None) if previous_event_time else None
    if not Path(file_path).name:
        return None

    interval_start = previous_event_time or (event_time - timedelta(minutes=15))
    interval_end = event_time
    spans: list[tuple[datetime, datetime]] = []
    apps: set[str] = set()
    for session in matching_file_window_sessions(file_path, window_sessions):
        start = getattr(session, "start_time", None)
        if not isinstance(start, datetime):
            continue
        start = start.replace(tzinfo=None)
        end = getattr(session, "end_time", None)
        if isinstance(end, datetime):
            end = end.replace(tzinfo=None)
        else:
            duration = getattr(session, "duration_seconds", None)
            end = start + timedelta(seconds=max(0.0, float(duration or 0)))
        left, right = max(interval_start, start), min(interval_end, end)
        contributed = False
        if right > left:
            spans.append((left, right))
            contributed = True
        elif start == event_time and (getattr(session, "duration_seconds", 0) or end > start):
            # A sampled window may begin at the same timestamp as the file
            # event. Preserve one second of directly observed overlap.
            spans.append((event_time, event_time + timedelta(seconds=1)))
            contributed = True
        app = str(getattr(session, "app_name", "") or "").strip()
        if contributed and app:
            apps.add(app)

    if not spans:
        return None
    spans.sort()
    merged: list[tuple[datetime, datetime]] = []
    for left, right in spans:
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    return {
        "seconds": round(sum((right - left).total_seconds() for left, right in merged)),
        "apps": sorted(apps),
        "start": merged[0][0],
        "end": merged[-1][1],
            "matched_by": "window title contains the tracked filename",
    }
