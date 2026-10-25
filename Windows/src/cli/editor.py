"""
SENTIENCE Editor Launcher.

Launches the user's chosen editor for a given file path.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path


def launch_editor(editor_cmd: str, file_path: Path | str) -> bool:
    """Launch user configured editor with the specified file."""
    default_editor = "notepad.exe" if os.name == "nt" else "nano"
    cmd = (editor_cmd or "").strip() or os.environ.get("EDITOR", default_editor)
    try:
        parts = shlex.split(cmd) + [str(file_path)]
        subprocess.run(parts, check=False)
        return True
    except (FileNotFoundError, OSError):
        # Fallback to $EDITOR if available and different from failed command
        fallback = os.environ.get("EDITOR", "").strip()
        if fallback and fallback != cmd:
            try:
                parts = shlex.split(fallback) + [str(file_path)]
                subprocess.run(parts, check=False)
                return True
            except (FileNotFoundError, OSError):
                pass
        return False
