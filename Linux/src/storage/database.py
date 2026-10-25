"""
SENTIENCE Database (SQLite + WAL).

Thread-safe SQLite manager. Stores tracked folders, file events,
window sessions, shell commands, git snapshots, chats, messages,
memory items, and audit events.  Uses WAL for concurrent access.
"""

import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Union
import json

from storage.models import (
    Chat,
    FileEvent,
    FileEventType,
    GitSnapshot,
    MemoryItem,
    Message,
    PrivacyLabel,
    ShellCommand,
    TrackedFolder,
    TrackingStatus,
    WindowSession,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# Schema
# ═══════════════════════════════════════════════════════════

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracked_folders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT    UNIQUE NOT NULL,
    label       TEXT    NOT NULL DEFAULT 'PUBLIC',
    status      TEXT    NOT NULL DEFAULT 'ACTIVE',
    read_allowed  INTEGER DEFAULT 1,
    edit_allowed  INTEGER DEFAULT 0,
    index_allowed INTEGER DEFAULT 1,
    track_allowed INTEGER DEFAULT 1,
    history_allowed INTEGER DEFAULT 1,
    model_scope   TEXT    DEFAULT 'hybrid',
    exclusions    TEXT    DEFAULT '',
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS file_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_id     INTEGER NOT NULL,
    event_type    TEXT    NOT NULL,
    file_path     TEXT    NOT NULL,
    old_path      TEXT,
    timestamp     TEXT    NOT NULL,
    git_commit    TEXT,
    lines_added   INTEGER DEFAULT 0,
    lines_removed INTEGER DEFAULT 0,
    is_directory  INTEGER DEFAULT 0,
    source        TEXT    DEFAULT 'filesystem_watcher',
    session_id    TEXT,
    FOREIGN KEY (folder_id) REFERENCES tracked_folders(id)
);

CREATE TABLE IF NOT EXISTS window_sessions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    app_name         TEXT    NOT NULL,
    window_title     TEXT    NOT NULL,
    start_time       TEXT    NOT NULL,
    end_time         TEXT,
    duration_seconds REAL,
    time_str         TEXT,
    is_idle          INTEGER DEFAULT 0,
    capture_completeness TEXT DEFAULT 'full'
);

CREATE TABLE IF NOT EXISTS shell_commands (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    shell_type        TEXT    NOT NULL,
    command           TEXT    NOT NULL,
    working_directory TEXT    NOT NULL,
    exit_code         INTEGER,
    timestamp         TEXT    NOT NULL,
    duration_seconds  REAL
);

CREATE TABLE IF NOT EXISTS git_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_id     INTEGER NOT NULL,
    commit_hash   TEXT    NOT NULL,
    timestamp     TEXT    NOT NULL,
    message       TEXT    DEFAULT '',
    files_changed INTEGER DEFAULT 0,
    lines_added   INTEGER DEFAULT 0,
    lines_removed INTEGER DEFAULT 0,
    FOREIGN KEY (folder_id) REFERENCES tracked_folders(id)
);

CREATE TABLE IF NOT EXISTS chats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL DEFAULT 'New Chat',
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    archived    INTEGER DEFAULT 0,
    project     TEXT,
    default_route TEXT,
    workspace_path TEXT,
    workspace_read INTEGER DEFAULT 0,
    workspace_write INTEGER DEFAULT 0,
    active_skill_ids TEXT DEFAULT '[]',
    enabled_modes TEXT DEFAULT '["chat"]',
    active_mode TEXT,
    active_model_id TEXT,
    enabled_local_sources TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    role        TEXT    NOT NULL,
    body        TEXT    NOT NULL,
    timestamp   TEXT    NOT NULL,
    provider    TEXT,
    model_id    TEXT,
    prompt_tokens INTEGER,
    output_tokens INTEGER,
    context_limit INTEGER,
    runtime_context INTEGER,
    local_sources_used TEXT DEFAULT '[]',
    FOREIGN KEY (chat_id) REFERENCES chats(id)
);

CREATE TABLE IF NOT EXISTS chat_document_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    source_path TEXT NOT NULL,
    source_name TEXT NOT NULL,
    page_number INTEGER,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding_json TEXT NOT NULL,
    embedding_model_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chat_attached_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    source_path TEXT NOT NULL,
    source_name TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE,
    UNIQUE(chat_id, source_path)
);

CREATE TABLE IF NOT EXISTS memory_items (
    id          TEXT    PRIMARY KEY,
    content     TEXT    NOT NULL,
    source      TEXT    DEFAULT 'observation',
    timestamp   TEXT    NOT NULL,
    is_explicit INTEGER DEFAULT 0,
    confidence  REAL    DEFAULT 0.5,
    times_seen  INTEGER DEFAULT 1,
    edit_state  TEXT    DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS tracking_state (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    state       TEXT    NOT NULL DEFAULT 'off',
    updated_at  TEXT    NOT NULL
);

-- Indices for fast lookups
CREATE INDEX IF NOT EXISTS idx_file_events_folder    ON file_events(folder_id);
CREATE INDEX IF NOT EXISTS idx_file_events_timestamp ON file_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_file_events_path      ON file_events(file_path);
CREATE INDEX IF NOT EXISTS idx_window_start          ON window_sessions(start_time);
CREATE INDEX IF NOT EXISTS idx_shell_timestamp       ON shell_commands(timestamp);
CREATE INDEX IF NOT EXISTS idx_git_snap_folder       ON git_snapshots(folder_id);
CREATE INDEX IF NOT EXISTS idx_messages_chat         ON messages(chat_id);
CREATE INDEX IF NOT EXISTS idx_chat_doc_chunks       ON chat_document_chunks(chat_id, source_path);
CREATE INDEX IF NOT EXISTS idx_chats_updated         ON chats(updated_at);
"""


class Database:
    """Thread-safe SQLite database manager for SENTIENCE."""

    def __init__(self, db_path: Union[Path, str]):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_schema()

    # ── Connection management ────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False
            )
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
        return self._local.conn

    def _init_schema(self) -> None:
        conn = self._get_conn()
        conn.executescript(SCHEMA)
        try:
            conn.execute("ALTER TABLE tracked_folders ADD COLUMN history_allowed INTEGER DEFAULT 1")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE tracked_folders ADD COLUMN model_scope TEXT DEFAULT 'hybrid'")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        for table, column, declaration in (
            ("chats", "workspace_path", "TEXT"),
            ("chats", "workspace_read", "INTEGER DEFAULT 0"),
            ("chats", "workspace_write", "INTEGER DEFAULT 0"),
            ("chats", "active_skill_ids", "TEXT DEFAULT '[]'"),
            ("chats", "enabled_modes", "TEXT DEFAULT '[\"chat\"]'"),
            ("chats", "active_mode", "TEXT"),
            ("chats", "active_model_id", "TEXT"),
            ("chats", "enabled_local_sources", "TEXT DEFAULT '[]'"),
            ("messages", "prompt_tokens", "INTEGER"),
            ("messages", "output_tokens", "INTEGER"),
            ("messages", "context_limit", "INTEGER"),
            ("messages", "runtime_context", "INTEGER"),
            ("messages", "local_sources_used", "TEXT DEFAULT '[]'"),
        ):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
                conn.commit()
            except sqlite3.OperationalError:
                pass
        # Initialize tracking state if not present
        conn.execute(
            "INSERT OR IGNORE INTO tracking_state (id, state, updated_at) VALUES (1, 'off', ?)",
            (datetime.now().isoformat(),),
        )
        conn.commit()

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

    def reset_database(self) -> None:
        """Completely wipe all tables and re-create fresh schema."""
        conn = self._get_conn()
        conn.execute("PRAGMA foreign_keys = OFF")
        cursor = conn.cursor()
        tables = [
            r[0] for r in cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        for t in tables:
            cursor.execute(f"DROP TABLE IF EXISTS \"{t}\"")
        conn.commit()
        conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()


    # ═════════════════════════════════════════════════════
    # Tracking State
    # ═════════════════════════════════════════════════════

    def get_tracking_state(self) -> str:
        row = self._get_conn().execute(
            "SELECT state FROM tracking_state WHERE id = 1"
        ).fetchone()
        return row["state"] if row else "off"

    def set_tracking_state(self, state: str) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE tracking_state SET state = ?, updated_at = ? WHERE id = 1",
            (state, datetime.now().isoformat()),
        )
        conn.commit()

    # ═════════════════════════════════════════════════════
    # Tracked Folders
    # ═════════════════════════════════════════════════════

    def add_folder(self, folder: TrackedFolder) -> TrackedFolder:
        conn = self._get_conn()
        now = datetime.now().isoformat()
        cur = conn.execute(
            "INSERT INTO tracked_folders (path, label, status, read_allowed, edit_allowed, "
            "index_allowed, track_allowed, history_allowed, model_scope, exclusions, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(folder.path), folder.label.value, folder.status.value,
                int(folder.read_allowed), int(folder.edit_allowed),
                int(folder.index_allowed), int(folder.track_allowed),
                int(folder.history_allowed), str(folder.model_scope or "hybrid"),
                folder.exclusions, now, now,
            ),
        )
        conn.commit()
        folder.id = cur.lastrowid
        return folder

    def get_folder(self, folder_id: int) -> Optional[TrackedFolder]:
        row = self._get_conn().execute(
            "SELECT * FROM tracked_folders WHERE id = ?", (folder_id,)
        ).fetchone()
        return self._row_to_folder(row) if row else None

    def get_folder_by_path(self, path: str) -> Optional[TrackedFolder]:
        row = self._get_conn().execute(
            "SELECT * FROM tracked_folders WHERE path = ?", (str(path),)
        ).fetchone()
        return self._row_to_folder(row) if row else None

    def get_all_folders(self) -> List[TrackedFolder]:
        rows = self._get_conn().execute("SELECT * FROM tracked_folders").fetchall()
        return [self._row_to_folder(r) for r in rows]

    def get_active_folders(self) -> List[TrackedFolder]:
        rows = self._get_conn().execute(
            "SELECT * FROM tracked_folders WHERE status = 'ACTIVE'"
        ).fetchall()
        return [self._row_to_folder(r) for r in rows]

    def update_folder_status(self, folder_id: int, status: TrackingStatus) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE tracked_folders SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, datetime.now().isoformat(), folder_id),
        )
        conn.commit()

    def update_folder_label(self, folder_id: int, label: PrivacyLabel) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE tracked_folders SET label = ?, updated_at = ? WHERE id = ?",
            (label.value, datetime.now().isoformat(), folder_id),
        )
        conn.commit()

    def remove_folder(self, folder_id: int) -> None:
        conn = self._get_conn()
        conn.execute("DELETE FROM file_events WHERE folder_id = ?", (folder_id,))
        conn.execute("DELETE FROM git_snapshots WHERE folder_id = ?", (folder_id,))
        conn.execute("DELETE FROM tracked_folders WHERE id = ?", (folder_id,))
        conn.commit()

    def delete_folder_history(self, folder_id: int) -> None:
        conn = self._get_conn()
        conn.execute("DELETE FROM file_events WHERE folder_id = ?", (folder_id,))
        conn.execute("DELETE FROM git_snapshots WHERE folder_id = ?", (folder_id,))
        conn.commit()

    def update_folder_permissions(
        self, folder_id: int, track: bool, read: bool, edit: bool, index: bool, history: bool, model_scope: str = "hybrid"
    ) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE tracked_folders SET track_allowed = ?, read_allowed = ?, "
            "edit_allowed = ?, index_allowed = ?, history_allowed = ?, model_scope = ?, updated_at = ? WHERE id = ?",
            (int(track), int(read), int(edit), int(index), int(history), str(model_scope or "hybrid"), datetime.now().isoformat(), folder_id),
        )
        conn.commit()

    # Convenience aliases for archive command
    get_tracked_folders = get_all_folders
    add_tracked_folder = add_folder
    remove_tracked_folder = remove_folder

    def _row_to_folder(self, row: sqlite3.Row) -> TrackedFolder:
        return TrackedFolder(
            id=row["id"],
            path=row["path"],
            label=PrivacyLabel(row["label"]),
            status=TrackingStatus(row["status"]),
            read_allowed=bool(row["read_allowed"]) if "read_allowed" in row.keys() else True,
            edit_allowed=bool(row["edit_allowed"]) if "edit_allowed" in row.keys() else False,
            index_allowed=bool(row["index_allowed"]) if "index_allowed" in row.keys() else True,
            track_allowed=bool(row["track_allowed"]) if "track_allowed" in row.keys() else True,
            history_allowed=bool(row["history_allowed"]) if "history_allowed" in row.keys() else True,
            model_scope=row["model_scope"] if ("model_scope" in row.keys() and row["model_scope"]) else "hybrid",
            exclusions=row["exclusions"] if "exclusions" in row.keys() else "",
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # ═════════════════════════════════════════════════════
    # File Events
    # ═════════════════════════════════════════════════════

    def add_file_event(self, event: FileEvent) -> FileEvent:
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO file_events (folder_id, event_type, file_path, old_path, "
            "timestamp, git_commit, lines_added, lines_removed, is_directory, source, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.folder_id, event.event_type.value, event.file_path,
                event.old_path, event.timestamp.isoformat(), event.git_commit,
                event.lines_added, event.lines_removed, int(event.is_directory),
                event.source, event.session_id,
            ),
        )
        conn.commit()
        event.id = cur.lastrowid
        return event

    def annotate_file_event(
        self,
        event_id: int,
        *,
        git_commit: Optional[str],
        lines_added: int = 0,
        lines_removed: int = 0,
        source: Optional[str] = None,
    ) -> None:
        """Attach shadow-Git evidence to an already recorded filesystem event.

        The watcher records the real-time event first, while Argus may create
        the corresponding shadow-Git commit a moment later. Updating the
        existing row avoids presenting one user action as two separate events.
        """
        conn = self._get_conn()
        assignments = [
            "git_commit = ?",
            "lines_added = ?",
            "lines_removed = ?",
        ]
        params: list = [git_commit, lines_added, lines_removed]
        if source is not None:
            assignments.append("source = ?")
            params.append(source)
        params.append(event_id)
        conn.execute(
            f"UPDATE file_events SET {', '.join(assignments)} WHERE id = ?",
            params,
        )
        conn.commit()

    def get_file_events(
        self,
        folder_id: Optional[int] = None,
        file_path: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: Optional[int] = 500,
        until: Optional[datetime] = None,
        event_type: Optional[str] = None,
    ) -> List[FileEvent]:
        q = "SELECT * FROM file_events WHERE 1=1"
        params: list = []
        if folder_id is not None:
            q += " AND folder_id = ?"
            params.append(folder_id)
        if file_path is not None:
            q += " AND (file_path = ? OR file_path LIKE ?)"
            params.append(file_path)
            params.append(f"%{file_path}")
        if since is not None:
            q += " AND timestamp >= ?"
            params.append(since.isoformat())
        if until is not None:
            q += " AND timestamp <= ?"
            params.append(until.isoformat())
        if event_type:
            q += " AND event_type = ?"
            params.append(event_type)
        q += " ORDER BY timestamp DESC"
        if limit is not None:
            q += " LIMIT ?"
            params.append(limit)

        rows = self._get_conn().execute(q, params).fetchall()
        return [
            FileEvent(
                id=r["id"], folder_id=r["folder_id"],
                event_type=FileEventType(r["event_type"]),
                file_path=r["file_path"], old_path=r["old_path"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                git_commit=r["git_commit"],
                lines_added=r["lines_added"], lines_removed=r["lines_removed"],
                is_directory=bool(r["is_directory"]),
                source=r["source"] if "source" in r.keys() else "filesystem_watcher",
            )
            for r in rows
        ]

    # ═════════════════════════════════════════════════════
    # Window Sessions
    # ═════════════════════════════════════════════════════

    def add_window_session(self, session: WindowSession) -> WindowSession:
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO window_sessions (app_name, window_title, start_time, end_time, "
            "duration_seconds, time_str, is_idle, capture_completeness) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session.app_name, session.window_title,
                session.start_time.isoformat(),
                session.end_time.isoformat() if session.end_time else None,
                session.duration_seconds, session.time_str,
                int(session.is_idle), session.capture_completeness,
            ),
        )
        conn.commit()
        session.id = cur.lastrowid
        return session

    def update_window_session(self, session_id: int, end_time: datetime, duration: float) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE window_sessions SET end_time = ?, duration_seconds = ? WHERE id = ?",
            (end_time.isoformat(), duration, session_id),
        )
        conn.commit()

    update_window_session_end = update_window_session

    def delete_window_sessions(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> int:
        """Delete all window sessions, or sessions overlapping an inclusive range."""
        conn = self._get_conn()
        clauses = []
        params: list[str] = []
        if since is not None:
            clauses.append("(end_time IS NULL OR end_time >= ?)")
            params.append(since.isoformat())
        if until is not None:
            clauses.append("start_time <= ?")
            params.append(until.isoformat())
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        cur = conn.execute("DELETE FROM window_sessions" + where, params)
        conn.commit()
        return max(0, cur.rowcount)

    def get_window_sessions(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        app_name: Optional[str] = None,
        limit: Optional[int] = 500,
        title_contains: Optional[List[str]] = None,
    ) -> List[WindowSession]:
        q = "SELECT * FROM window_sessions WHERE 1=1"
        params: list = []
        if since:
            q += " AND (end_time IS NULL OR end_time >= ?)"
            params.append(since.isoformat())
        if until:
            q += " AND start_time <= ?"
            params.append(until.isoformat())
        if app_name:
            q += " AND app_name LIKE ?"
            params.append(f"%{app_name}%")
        if title_contains:
            title_terms = list(dict.fromkeys(term.strip() for term in title_contains if term.strip()))
            if title_terms:
                q += " AND (" + " OR ".join("window_title LIKE ?" for _ in title_terms) + ")"
                params.extend(f"%{term}%" for term in title_terms)
        q += " ORDER BY start_time DESC"
        if limit is not None:
            q += " LIMIT ?"
            params.append(limit)

        rows = self._get_conn().execute(q, params).fetchall()
        return [
            WindowSession(
                id=r["id"], app_name=r["app_name"],
                window_title=r["window_title"],
                start_time=datetime.fromisoformat(r["start_time"]),
                end_time=datetime.fromisoformat(r["end_time"]) if r["end_time"] else None,
                duration_seconds=r["duration_seconds"], time_str=r["time_str"],
            )
            for r in rows
        ]

    # ═════════════════════════════════════════════════════
    # Shell Commands
    # ═════════════════════════════════════════════════════

    def add_shell_command(self, cmd: ShellCommand) -> ShellCommand:
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO shell_commands (shell_type, command, working_directory, "
            "exit_code, timestamp, duration_seconds) VALUES (?, ?, ?, ?, ?, ?)",
            (
                cmd.shell_type, cmd.command, cmd.working_directory,
                cmd.exit_code, cmd.timestamp.isoformat(), cmd.duration_seconds,
            ),
        )
        conn.commit()
        cmd.id = cur.lastrowid
        return cmd

    def get_shell_commands(
        self, since: Optional[datetime] = None, working_directory: Optional[str] = None, limit: int = 100
    ) -> List[ShellCommand]:
        q = "SELECT * FROM shell_commands WHERE 1=1"
        params: list = []
        if since:
            q += " AND timestamp >= ?"
            params.append(since.isoformat())
        if working_directory:
            q += " AND working_directory = ?"
            params.append(working_directory)
        q += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        rows = self._get_conn().execute(q, params).fetchall()
        return [
            ShellCommand(
                id=r["id"], shell_type=r["shell_type"], command=r["command"],
                working_directory=r["working_directory"],
                exit_code=r["exit_code"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                duration_seconds=r["duration_seconds"],
            )
            for r in rows
        ]

    # ═════════════════════════════════════════════════════
    # Git Snapshots
    # ═════════════════════════════════════════════════════

    def add_git_snapshot(self, snap: GitSnapshot) -> GitSnapshot:
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO git_snapshots (folder_id, commit_hash, timestamp, message, "
            "files_changed, lines_added, lines_removed) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                snap.folder_id, snap.commit_hash, snap.timestamp.isoformat(),
                snap.message, snap.files_changed, snap.lines_added, snap.lines_removed,
            ),
        )
        conn.commit()
        snap.id = cur.lastrowid
        return snap

    def get_git_snapshots(self, folder_id: Optional[int] = None, limit: int = 50) -> List[GitSnapshot]:
        query = "SELECT * FROM git_snapshots"
        params = []
        if folder_id is not None:
            query += " WHERE folder_id = ?"
            params.append(folder_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = self._get_conn().execute(query, params).fetchall()
        return [
            GitSnapshot(
                id=r["id"],
                folder_id=r["folder_id"],
                commit_hash=r["commit_hash"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                message=r["message"],
                files_changed=r["files_changed"],
                lines_added=r["lines_added"],
                lines_removed=r["lines_removed"],
            )
            for r in rows
        ]

    # ═════════════════════════════════════════════════════
    # Chats & Messages
    # ═════════════════════════════════════════════════════

    def create_chat(
        self,
        name: str = "New Chat",
        project: str = None,
        workspace_path: Optional[str] = None,
        workspace_read: bool = False,
        workspace_write: bool = False,
        active_skill_ids: Optional[List[str]] = None,
        enabled_modes: Optional[List[str]] = None,
    ) -> Chat:
        conn = self._get_conn()
        now = datetime.now().isoformat()
        cur = conn.execute(
            "INSERT INTO chats (name, created_at, updated_at, project, workspace_path, workspace_read, workspace_write, active_skill_ids, enabled_modes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, now, now, project, workspace_path, int(workspace_read), int(workspace_write), json.dumps(active_skill_ids or []), json.dumps(enabled_modes or ["chat"])),
        )
        conn.commit()
        return Chat(
            id=cur.lastrowid, name=name, project=project,
            workspace_path=workspace_path, workspace_read=workspace_read,
            workspace_write=workspace_write,
            active_skill_ids=active_skill_ids or [],
            enabled_modes=enabled_modes or ["chat"],
        )

    def get_chats(self, include_archived: bool = False) -> List[Chat]:
        q = "SELECT * FROM chats"
        if not include_archived:
            q += " WHERE archived = 0"
        q += " ORDER BY updated_at DESC"
        rows = self._get_conn().execute(q).fetchall()
        chats = []
        for r in rows:
            chat_id = r["id"]
            msg_rows = self._get_conn().execute(
                "SELECT role, model_id, provider FROM messages WHERE chat_id = ?",
                (chat_id,)
            ).fetchall()
            models = []
            for m in msg_rows:
                if m["role"] != "assistant":
                    continue
                mod = m["model_id"] or m["provider"]
                if mod and mod not in models:
                    models.append(mod)
            chats.append(Chat(
                id=chat_id,
                name=r["name"],
                created_at=datetime.fromisoformat(r["created_at"]),
                updated_at=datetime.fromisoformat(r["updated_at"]),
                archived=bool(r["archived"]),
                project=r["project"],
                default_route=r["default_route"] if "default_route" in r.keys() else None,
                models_used=models,
                workspace_path=r["workspace_path"] if "workspace_path" in r.keys() else None,
                workspace_read=bool(r["workspace_read"]) if "workspace_read" in r.keys() else False,
                workspace_write=bool(r["workspace_write"]) if "workspace_write" in r.keys() else False,
                active_skill_ids=json.loads(r["active_skill_ids"] or "[]") if "active_skill_ids" in r.keys() else [],
                enabled_modes=json.loads(r["enabled_modes"] or '["chat"]') if "enabled_modes" in r.keys() else ["chat"],
                active_mode=r["active_mode"] if "active_mode" in r.keys() else None,
                active_model_id=r["active_model_id"] if "active_model_id" in r.keys() else None,
                enabled_local_sources=json.loads(r["enabled_local_sources"] or "[]") if "enabled_local_sources" in r.keys() else [],
            ))
        return chats

    def get_chat(self, chat_id: int) -> Optional[Chat]:
        row = self._get_conn().execute(
            "SELECT * FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()
        if not row:
            return None
        msg_rows = self._get_conn().execute(
            "SELECT role, model_id, provider FROM messages WHERE chat_id = ?",
            (chat_id,)
        ).fetchall()
        models = []
        for m in msg_rows:
            if m["role"] != "assistant":
                continue
            mod = m["model_id"] or m["provider"]
            if mod and mod not in models:
                models.append(mod)
        return Chat(
            id=row["id"],
            name=row["name"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            archived=bool(row["archived"]),
            project=row["project"],
            default_route=row["default_route"] if "default_route" in row.keys() else None,
            models_used=models,
            workspace_path=row["workspace_path"] if "workspace_path" in row.keys() else None,
            workspace_read=bool(row["workspace_read"]) if "workspace_read" in row.keys() else False,
            workspace_write=bool(row["workspace_write"]) if "workspace_write" in row.keys() else False,
            active_skill_ids=json.loads(row["active_skill_ids"] or "[]") if "active_skill_ids" in row.keys() else [],
            enabled_modes=json.loads(row["enabled_modes"] or '["chat"]') if "enabled_modes" in row.keys() else ["chat"],
            active_mode=row["active_mode"] if "active_mode" in row.keys() else None,
            active_model_id=row["active_model_id"] if "active_model_id" in row.keys() else None,
            enabled_local_sources=json.loads(row["enabled_local_sources"] or "[]") if "enabled_local_sources" in row.keys() else [],
        )

    def set_chat_local_sources(self, chat_id: int, sources: List[str]) -> None:
        allowed = [source for source in dict.fromkeys(sources) if source in ("argus", "windows")]
        conn = self._get_conn()
        row = conn.execute("SELECT enabled_local_sources FROM chats WHERE id = ?", (chat_id,)).fetchone()
        previous = json.loads(row["enabled_local_sources"] or "[]") if row else []
        removed = set(previous) - set(allowed)
        if removed:
            # Older transcripts predate per-message source provenance. Mark
            # answers to history questions so disabling can redact them from
            # future model context without deleting the visible chat history.
            import re
            rows = conn.execute(
                "SELECT id, role, body, local_sources_used FROM messages WHERE chat_id = ? ORDER BY timestamp, id",
                (chat_id,),
            ).fetchall()
            pending_history_question = False
            history_terms = re.compile(
                r"(?i)\b(argus|window|windows|history|activity|opened|what did i|what have i|what changed|"
                r"worked on|modified|created|deleted|renamed|moved|spent time|how long)\b"
            )
            for message in rows:
                if message["role"] == "user":
                    pending_history_question = bool(history_terms.search(message["body"] or ""))
                elif message["role"] == "assistant" and pending_history_question:
                    used = set(json.loads(message["local_sources_used"] or "[]"))
                    # Only tag sources being revoked. If a user switches from
                    # Argus to Windows, this keeps Windows-era answers usable.
                    used.update(removed)
                    conn.execute(
                        "UPDATE messages SET local_sources_used = ? WHERE id = ?",
                        (json.dumps(sorted(used)), message["id"]),
                    )
                    pending_history_question = False
        conn.execute("UPDATE chats SET enabled_local_sources = ?, updated_at = ? WHERE id = ?", (json.dumps(allowed), datetime.now().isoformat(), chat_id))
        conn.commit()

    def set_chat_model_selection(self, chat_id: int, mode: str, model_id: str) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE chats SET active_mode = ?, active_model_id = ?, updated_at = ? WHERE id = ?",
            (mode, model_id, datetime.now().isoformat(), chat_id),
        )
        conn.commit()

    def replace_chat_document_chunks(self, chat_id: int, source_path: str, chunks: List[dict]) -> None:
        conn = self._get_conn()
        conn.execute("DELETE FROM chat_document_chunks WHERE chat_id = ? AND source_path = ?", (chat_id, source_path))
        conn.executemany(
            "INSERT INTO chat_document_chunks (chat_id, source_path, source_name, page_number, chunk_index, content, embedding_json, embedding_model_id, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(
                chat_id, source_path, item["source_name"], item.get("page_number"), item["chunk_index"],
                item["content"], json.dumps(item["embedding"]), item["embedding_model_id"], item["content_hash"],
            ) for item in chunks],
        )
        conn.commit()

    def get_chat_document_chunks(self, chat_id: int) -> List[dict]:
        rows = self._get_conn().execute(
            "SELECT * FROM chat_document_chunks WHERE chat_id = ? ORDER BY source_name, page_number, chunk_index",
            (chat_id,),
        ).fetchall()
        return [{**dict(row), "embedding": json.loads(row["embedding_json"])} for row in rows]

    def get_chat_documents(self, chat_id: int) -> List[dict]:
        rows = self._get_conn().execute(
            "SELECT source_path, source_name, COUNT(*) AS chunks, MAX(content_hash) AS content_hash, MAX(embedding_model_id) AS embedding_model_id "
            "FROM chat_document_chunks WHERE chat_id = ? GROUP BY source_path, source_name ORDER BY source_name",
            (chat_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def save_chat_attachment(self, chat_id: int, source_path: str, source_name: str, content: str, content_hash: str) -> None:
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO chat_attached_files (chat_id, source_path, source_name, content, content_hash) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(chat_id, source_path) DO UPDATE SET source_name=excluded.source_name, content=excluded.content, content_hash=excluded.content_hash",
            (chat_id, source_path, source_name, content, content_hash),
        )
        conn.commit()

    def get_chat_attachments(self, chat_id: int) -> List[dict]:
        rows = self._get_conn().execute(
            "SELECT source_path, source_name, content, content_hash FROM chat_attached_files WHERE chat_id = ? ORDER BY source_name",
            (chat_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def remove_chat_attachment(self, chat_id: int, source_path: str) -> None:
        conn = self._get_conn()
        conn.execute("DELETE FROM chat_attached_files WHERE chat_id = ? AND source_path = ?", (chat_id, source_path))
        conn.commit()

    def remove_chat_document(self, chat_id: int, source_path: str) -> None:
        conn = self._get_conn()
        conn.execute("DELETE FROM chat_document_chunks WHERE chat_id = ? AND source_path = ?", (chat_id, source_path))
        conn.commit()

    def clear_chat_context(self, chat_id: int, clear_files: bool = False) -> None:
        """Forget the conversation transcript, optionally detaching all added files."""
        conn = self._get_conn()
        conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        if clear_files:
            conn.execute("DELETE FROM chat_document_chunks WHERE chat_id = ?", (chat_id,))
            conn.execute("DELETE FROM chat_attached_files WHERE chat_id = ?", (chat_id,))
        conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (datetime.now().isoformat(), chat_id))
        conn.commit()

    def rename_chat(self, chat_id: int, name: str) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE chats SET name = ?, updated_at = ? WHERE id = ?",
            (name, datetime.now().isoformat(), chat_id),
        )
        conn.commit()

    def update_chat_workspace(
        self,
        chat_id: int,
        workspace_path: Optional[str],
        workspace_read: bool,
        workspace_write: bool,
    ) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE chats SET workspace_path = ?, workspace_read = ?, workspace_write = ?, updated_at = ? WHERE id = ?",
            (workspace_path, int(workspace_read), int(workspace_write), datetime.now().isoformat(), chat_id),
        )
        conn.commit()

    def set_chat_active_skills(self, chat_id: int, skill_ids: List[str]) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE chats SET active_skill_ids = ?, updated_at = ? WHERE id = ?",
            (json.dumps(list(dict.fromkeys(skill_ids))), datetime.now().isoformat(), chat_id),
        )
        conn.commit()

    def archive_chat(self, chat_id: int) -> None:
        conn = self._get_conn()
        conn.execute("UPDATE chats SET archived = 1 WHERE id = ?", (chat_id,))
        conn.commit()

    def delete_chat(self, chat_id: int) -> None:
        conn = self._get_conn()
        conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        conn.commit()

    def add_message(self, msg: Message) -> Message:
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO messages (chat_id, role, body, timestamp, provider, model_id, prompt_tokens, output_tokens, context_limit, runtime_context, local_sources_used) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                msg.chat_id, msg.role, msg.body, msg.timestamp.isoformat(),
                msg.provider, msg.model_id, msg.prompt_tokens, msg.output_tokens,
                msg.context_limit, msg.runtime_context,
                json.dumps(msg.local_sources_used),
            ),
        )
        conn.execute(
            "UPDATE chats SET updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), msg.chat_id),
        )
        conn.commit()
        msg.id = cur.lastrowid
        return msg

    def get_messages(self, chat_id: int) -> List[Message]:
        rows = self._get_conn().execute(
            "SELECT * FROM messages WHERE chat_id = ? ORDER BY timestamp, id",
            (chat_id,),
        ).fetchall()
        return [
            Message(
                id=r["id"], chat_id=r["chat_id"], role=r["role"],
                body=r["body"], timestamp=datetime.fromisoformat(r["timestamp"]),
                provider=r["provider"], model_id=r["model_id"],
                prompt_tokens=r["prompt_tokens"] if "prompt_tokens" in r.keys() else None,
                output_tokens=r["output_tokens"] if "output_tokens" in r.keys() else None,
                context_limit=r["context_limit"] if "context_limit" in r.keys() else None,
                runtime_context=r["runtime_context"] if "runtime_context" in r.keys() else None,
                local_sources_used=json.loads(r["local_sources_used"] or "[]") if "local_sources_used" in r.keys() else [],
            )
            for r in rows
        ]

    # ═════════════════════════════════════════════════════
    # Memory Items (boss)
    # ═════════════════════════════════════════════════════

    def add_memory_item(self, item: MemoryItem) -> MemoryItem:
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO memory_items (id, content, source, timestamp, "
            "is_explicit, confidence, times_seen, edit_state) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item.id, item.content, item.source, item.timestamp.isoformat(),
                int(item.is_explicit), item.confidence, item.times_seen,
                item.edit_state,
            ),
        )
        conn.commit()
        return item

    def get_memory_items(self, include_deleted: bool = False) -> List[MemoryItem]:
        q = "SELECT * FROM memory_items"
        if not include_deleted:
            q += " WHERE edit_state = 'active'"
        q += " ORDER BY timestamp DESC"
        rows = self._get_conn().execute(q).fetchall()
        return [
            MemoryItem(
                id=r["id"], content=r["content"], source=r["source"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                is_explicit=bool(r["is_explicit"]),
                confidence=r["confidence"], times_seen=r["times_seen"],
                edit_state=r["edit_state"],
            )
            for r in rows
        ]

    def delete_memory_item(self, item_id: str) -> bool:
        conn = self._get_conn()
        cur = conn.execute(
            "UPDATE memory_items SET edit_state = 'deleted' WHERE id = ?",
            (item_id,),
        )
        conn.commit()
        return cur.rowcount > 0

    # ═════════════════════════════════════════════════════
    # Cleanup
    # ═════════════════════════════════════════════════════

    def cleanup_old_records(self, retention_days: int) -> int:
        cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()
        conn = self._get_conn()
        total = 0
        for table, col in [
            ("file_events", "timestamp"),
            ("window_sessions", "start_time"),
            ("shell_commands", "timestamp"),
        ]:
            cur = conn.execute(f"DELETE FROM {table} WHERE {col} < ?", (cutoff,))
            total += cur.rowcount
        conn.commit()
        return total

    def clear_database(self) -> None:
        conn = self._get_conn()
        for table in [
            "file_events", "window_sessions", "shell_commands",
            "git_snapshots", "messages", "chats", "memory_items",
        ]:
            conn.execute(f"DELETE FROM {table}")
        conn.commit()


# Backward compatibility alias
MemoryDatabase = Database

