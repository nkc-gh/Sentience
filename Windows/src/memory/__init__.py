"""
SENTIENCE Memory and Computer Awareness.
Provides historical awareness, database storage, and activity tracking.
"""

from storage.database import Database as MemoryDatabase, Database
from computer.documents import extract_text, is_text_file
from computer.filesystem import FilesystemCollector, _should_ignore
from computer.git_history import ShadowGitManager
from computer.queries import MemoryQuery
from computer.shell_integration import ShellIntegration
from computer.directory_registry import TrackedFolderManager
from computer.window_tracker import WindowTracker, get_active_window

__all__ = [
    "MemoryDatabase",
    "Database",
    "extract_text",
    "is_text_file",
    "FilesystemCollector",
    "_should_ignore",
    "ShadowGitManager",
    "MemoryQuery",
    "ShellIntegration",
    "TrackedFolderManager",
    "WindowTracker",
    "get_active_window",
]
