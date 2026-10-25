"""
Memory Query Interface.

High-level read-only tools designed to be called by the SENTIENCE
agent.  Each method returns a structured dict that can be serialised
directly into an LLM tool-call response.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from storage.database import Database
from computer.git_history import ShadowGitManager

logger = logging.getLogger(__name__)


def _fmt_duration(seconds: float) -> str:
    """Human-readable duration string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m:.0f}m {s:.0f}s"
    h, rem = divmod(seconds, 3600)
    m = rem / 60
    return f"{h:.0f}h {m:.0f}m"


class MemoryQuery:
    """Stateless query façade over the Memory database + shadow Git."""

    def __init__(self, db: Database, git: ShadowGitManager):
        self.db = db
        self.git = git

    # ═════════════════════════════════════════════════════
    # timeline()
    # ═════════════════════════════════════════════════════

    def timeline(
        self, hours: int = 24, folder_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Unified timeline of file, window, and shell activity."""
        since = datetime.now() - timedelta(hours=hours)

        file_events = self.db.get_file_events(
            folder_id=folder_id, since=since, limit=200
        )
        window_sessions = self.db.get_window_sessions(since=since, limit=100)
        shell_commands = self.db.get_shell_commands(since=since, limit=100)

        items: List[Dict[str, Any]] = []

        for e in file_events:
            items.append(
                {
                    "type": "file",
                    "timestamp": e.timestamp.isoformat(),
                    "event_type": e.event_type.value,
                    "path": e.file_path,
                    "old_path": e.old_path,
                    "lines_added": e.lines_added,
                    "lines_removed": e.lines_removed,
                }
            )
        for s in window_sessions:
            items.append(
                {
                    "type": "window",
                    "timestamp": s.start_time.isoformat(),
                    "time_str": s.time_str or s.start_time.strftime("%I:%M %p").lstrip("0"),
                    "app_name": s.app_name,
                    "window_title": s.window_title,
                    "duration": _fmt_duration(s.duration_seconds or 0),
                    "end_time": s.end_time.isoformat() if s.end_time else None,
                }
            )
        for c in shell_commands:
            items.append(
                {
                    "type": "shell",
                    "timestamp": c.timestamp.isoformat(),
                    "command": c.command,
                    "working_directory": c.working_directory,
                    "exit_code": c.exit_code,
                    "duration_seconds": c.duration_seconds,
                }
            )

        items.sort(key=lambda x: x["timestamp"], reverse=True)

        return {
            "period": f"Last {hours} hours",
            "since": since.isoformat(),
            "total_events": len(items),
            "file_events": len(file_events),
            "window_sessions": len(window_sessions),
            "shell_commands": len(shell_commands),
            "items": items,
        }

    # ═════════════════════════════════════════════════════
    # search()
    # ═════════════════════════════════════════════════════

    def search(self, query: str, hours: int = 72) -> Dict[str, Any]:
        """Free-text search across all memory types."""
        since = datetime.now() - timedelta(hours=hours)
        q = query.lower()
        matches: List[Dict[str, Any]] = []

        for e in self.db.get_file_events(since=since, limit=500):
            if q in e.file_path.lower():
                matches.append(
                    {
                        "type": "file_event",
                        "timestamp": e.timestamp.isoformat(),
                        "event_type": e.event_type.value,
                        "path": e.file_path,
                    }
                )

        for s in self.db.get_window_sessions(since=since, limit=200):
            if q in s.app_name.lower() or q in s.window_title.lower():
                matches.append(
                    {
                        "type": "window_session",
                        "timestamp": s.start_time.isoformat(),
                        "app_name": s.app_name,
                        "window_title": s.window_title,
                        "duration": _fmt_duration(s.duration_seconds or 0),
                    }
                )

        for c in self.db.get_shell_commands(since=since, limit=200):
            if q in c.command.lower() or q in c.working_directory.lower():
                matches.append(
                    {
                        "type": "shell_command",
                        "timestamp": c.timestamp.isoformat(),
                        "command": c.command,
                        "working_directory": c.working_directory,
                    }
                )

        return {"query": query, "total_matches": len(matches), "matches": matches}

    # ═════════════════════════════════════════════════════
    # file_history()
    # ═════════════════════════════════════════════════════

    def file_history(
        self, file_path: str, folder_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Full event + git history for a single file."""
        until = datetime.now()
        since = until - timedelta(days=max(1, int(getattr(self.git.config, "memory_retention_days", 45))))
        events = self.db.get_file_events(
            folder_id=folder_id, file_path=file_path, since=since, until=until, limit=None
        )

        git_history: List[Dict] = []
        if folder_id is not None:
            folder = self.db.get_folder(folder_id)
            if folder:
                git_history = self.git.file_history(
                    folder.path, file_path, limit=None, since=since, until=until,
                )

        return {
            "file": file_path,
            "events": [
                {
                    "timestamp": e.timestamp.isoformat(),
                    "event_type": e.event_type.value,
                    "lines_added": e.lines_added,
                    "lines_removed": e.lines_removed,
                    "git_commit": e.git_commit,
                }
                for e in events
            ],
            "git_history": git_history,
        }

    # ═════════════════════════════════════════════════════
    # diff()
    # ═════════════════════════════════════════════════════

    def diff(
        self,
        folder_id: int,
        commit1: Optional[str] = None,
        commit2: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> str:
        folder = self.db.get_folder(folder_id)
        if not folder:
            return "Folder not found."
        return self.git.diff(folder.path, commit1, commit2, file_path)

    # ═════════════════════════════════════════════════════
    # recent_changes()
    # ═════════════════════════════════════════════════════

    def recent_changes(
        self, hours: int = 24, folder_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Aggregated file-change summary."""
        since = datetime.now() - timedelta(hours=hours)
        events = self.db.get_file_events(
            folder_id=folder_id, since=since, limit=500
        )

        by_file: Dict[str, Dict[str, Any]] = {}
        for e in events:
            entry = by_file.setdefault(
                e.file_path,
                {
                    "path": e.file_path,
                    "events": [],
                    "total_lines_added": 0,
                    "total_lines_removed": 0,
                },
            )
            entry["events"].append(e.event_type.value)
            entry["total_lines_added"] += e.lines_added
            entry["total_lines_removed"] += e.lines_removed

        return {
            "period": f"Last {hours} hours",
            "files_changed": len(by_file),
            "total_events": len(events),
            "files": list(by_file.values()),
        }

    # ═════════════════════════════════════════════════════
    # recently_deleted()
    # ═════════════════════════════════════════════════════

    def recently_deleted(
        self, hours: int = 72, folder_id: Optional[int] = None
    ) -> List[Dict]:
        since = datetime.now() - timedelta(hours=hours)
        events = self.db.get_file_events(
            folder_id=folder_id, since=since, limit=500
        )
        return [
            {
                "path": e.file_path,
                "timestamp": e.timestamp.isoformat(),
                "is_directory": e.is_directory,
            }
            for e in events
            if e.event_type.value == "DELETED"
        ]

    # ═════════════════════════════════════════════════════
    # window_history()
    # ═════════════════════════════════════════════════════

    def window_history(
        self, hours: int = 24, app_name: Optional[str] = None
    ) -> Dict[str, Any]:
        since = datetime.now() - timedelta(hours=hours)
        sessions = self.db.get_window_sessions(
            since=since, app_name=app_name, limit=200
        )

        totals: Dict[str, float] = {}
        for s in sessions:
            dur = s.duration_seconds or 0
            totals[s.app_name] = totals.get(s.app_name, 0) + dur

        return {
            "period": f"Last {hours} hours",
            "total_sessions": len(sessions),
            "app_summary": [
                {"app": app, "total_seconds": t, "total_formatted": _fmt_duration(t)}
                for app, t in sorted(totals.items(), key=lambda x: -x[1])
            ],
            "sessions": [
                {
                    "app_name": s.app_name,
                    "window_title": s.window_title,
                    "start_time": s.start_time.isoformat(),
                    "time_str": s.time_str or s.start_time.strftime("%I:%M %p").lstrip("0"),
                    "end_time": s.end_time.isoformat() if s.end_time else None,
                    "duration": _fmt_duration(s.duration_seconds or 0),
                }
                for s in sessions
            ],
        }

    # ═════════════════════════════════════════════════════
    # shell_history()
    # ═════════════════════════════════════════════════════

    def shell_history(
        self, hours: int = 24, working_directory: Optional[str] = None
    ) -> Dict[str, Any]:
        since = datetime.now() - timedelta(hours=hours)
        commands = self.db.get_shell_commands(
            since=since, working_directory=working_directory, limit=200
        )
        return {
            "period": f"Last {hours} hours",
            "total_commands": len(commands),
            "commands": [
                {
                    "command": c.command,
                    "working_directory": c.working_directory,
                    "shell": c.shell_type,
                    "exit_code": c.exit_code,
                    "timestamp": c.timestamp.isoformat(),
                    "duration_seconds": c.duration_seconds,
                }
                for c in commands
            ],
        }
