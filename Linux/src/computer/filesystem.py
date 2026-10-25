"""
Filesystem Activity Collector.

Uses *watchdog* to observe create / modify / delete / rename / move
events inside tracked folders.  Raw events are debounced and
normalised (e.g. temp-file-create → rename collapses into a single
MODIFIED event) before being committed to the database.
"""

import logging
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from config import SentienceConfig
from storage.database import Database
from storage.models import FileEvent, FileEventType

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# Ignore rules
# ═══════════════════════════════════════════════════════════

_IGNORE_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    ".sentience",
    ".venv",
    "venv",
    ".tox",
    ".eggs",
    "dist",
    "build",
    ".cache",
}

_IGNORE_SUFFIXES = {
    ".pyc", ".pyo", ".swp", ".swo", ".swn",
    ".tmp", ".temp", ".bak", ".lock",
}


def _should_ignore(path: str) -> bool:
    """Return True if *path* should be silently dropped."""
    p = Path(path)
    # Directory components
    for part in p.parts:
        if part in _IGNORE_DIRS:
            return True
    # File-level checks
    name = p.name
    if name.endswith("~"):
        return True
    if name.startswith(".#") or name.startswith(".goutputstream"):
        return True
    if p.suffix in _IGNORE_SUFFIXES:
        return True
    return False


# ═══════════════════════════════════════════════════════════
# Pending-event container (used during debounce window)
# ═══════════════════════════════════════════════════════════


class _PendingEvent:
    __slots__ = ("event_type", "path", "old_path", "is_dir",
                 "timestamp", "last_touch")

    def __init__(
        self,
        event_type: FileEventType,
        path: str,
        old_path: Optional[str] = None,
        is_dir: bool = False,
    ):
        self.event_type = event_type
        self.path = path              # The current / new path
        self.old_path = old_path      # Previous path (rename / move)
        self.is_dir = is_dir
        self.timestamp = datetime.now()
        self.last_touch = time.monotonic()


# ═══════════════════════════════════════════════════════════
# Debounced watchdog handler
# ═══════════════════════════════════════════════════════════


class _DebouncedHandler(FileSystemEventHandler):
    """Collects raw watchdog events and flushes them to the DB after a
    quiet period (the *debounce window*)."""

    def __init__(
        self,
        folder_id: int,
        folder_path: str,
        db: Database,
        debounce_seconds: float = 2.0,
    ):
        super().__init__()
        self.folder_id = folder_id
        self.folder_path = folder_path
        self.db = db
        self.debounce_seconds = debounce_seconds

        self._pending: Dict[str, _PendingEvent] = {}
        self._lock = threading.Lock()
        self._flush_timer: Optional[threading.Timer] = None

    # ── Scheduling ───────────────────────────────────────

    def _schedule_flush(self) -> None:
        if self._flush_timer is not None:
            self._flush_timer.cancel()
        self._flush_timer = threading.Timer(self.debounce_seconds, self._flush)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _flush(self) -> None:
        """Write matured pending events to the database."""
        now = time.monotonic()
        to_flush: List[_PendingEvent] = []
        has_remaining = False

        with self._lock:
            remaining: Dict[str, _PendingEvent] = {}
            for key, pending in self._pending.items():
                if now - pending.last_touch >= self.debounce_seconds:
                    to_flush.append(pending)
                else:
                    remaining[key] = pending
            self._pending = remaining
            has_remaining = bool(remaining)

        for pending in to_flush:
            try:
                base = Path(self.folder_path)
                p_rel = Path(pending.path)
                if base.is_dir():
                    try:
                        rel_path = str(p_rel.relative_to(base))
                    except ValueError:
                        rel_path = p_rel.name
                else:
                    rel_path = p_rel.name

                old_rel: Optional[str] = None
                if pending.old_path and pending.event_type in (
                    FileEventType.RENAMED,
                    FileEventType.MOVED,
                ):
                    p_old = Path(pending.old_path)
                    if base.is_dir():
                        try:
                            old_rel = str(p_old.relative_to(base))
                        except ValueError:
                            old_rel = p_old.name
                    else:
                        old_rel = p_old.name

                event = FileEvent(
                    folder_id=self.folder_id,
                    event_type=pending.event_type,
                    file_path=rel_path,
                    old_path=old_rel,
                    timestamp=pending.timestamp,
                    is_directory=pending.is_dir,
                )
                self.db.add_file_event(event)
                logger.debug("Recorded: %s %s", event.event_type.value, rel_path)
            except Exception:
                logger.exception("Failed to record filesystem event")

        if has_remaining:
            self._schedule_flush()

    # ── Event intake (called by watchdog) ────────────────

    def _handle(
        self,
        event_type: FileEventType,
        src_path: str,
        old_path: Optional[str] = None,
        is_dir: bool = False,
    ) -> None:
        if _should_ignore(src_path):
            return
        if old_path and _should_ignore(old_path):
            return

        if not Path(self.folder_path).is_dir():
            try:
                if Path(src_path).resolve() != Path(self.folder_path).resolve():
                    return
            except Exception:
                return

        with self._lock:
            key = src_path

            if key in self._pending:
                prev = self._pending[key]
                # CREATE then MODIFY → keep as CREATE
                if (
                    prev.event_type == FileEventType.CREATED
                    and event_type == FileEventType.MODIFIED
                ):
                    prev.last_touch = time.monotonic()
                    self._schedule_flush()
                    return
                # CREATE then DELETE → temp file; discard both
                if (
                    prev.event_type == FileEventType.CREATED
                    and event_type == FileEventType.DELETED
                ):
                    del self._pending[key]
                    self._schedule_flush()
                    return

            self._pending[key] = _PendingEvent(
                event_type, src_path, old_path, is_dir
            )

        self._schedule_flush()

    # ── watchdog callbacks ───────────────────────────────

    def on_created(self, event):  # type: ignore[override]
        self._handle(
            FileEventType.CREATED,
            event.src_path,
            is_dir=isinstance(event, DirCreatedEvent),
        )

    def on_modified(self, event):  # type: ignore[override]
        if event.is_directory:
            return  # directory-modify is noisy and meaningless
        self._handle(FileEventType.MODIFIED, event.src_path)

    def on_deleted(self, event):  # type: ignore[override]
        self._handle(
            FileEventType.DELETED,
            event.src_path,
            is_dir=isinstance(event, DirDeletedEvent),
        )

    def on_moved(self, event):  # type: ignore[override]
        is_dir = isinstance(event, DirMovedEvent)
        src_parent = str(Path(event.src_path).parent)
        dest_parent = str(Path(event.dest_path).parent)
        etype = (
            FileEventType.RENAMED
            if src_parent == dest_parent
            else FileEventType.MOVED
        )
        # Store the *new* path as the primary key, old path as reference.
        self._handle(etype, event.dest_path, event.src_path, is_dir=is_dir)


# ═══════════════════════════════════════════════════════════
# Public collector (manages one Observer per tracked folder)
# ═══════════════════════════════════════════════════════════


class FilesystemCollector:
    """Start / stop watchdog observers for tracked folders."""

    def __init__(self, db: Database, config: SentienceConfig):
        self.db = db
        self.config = config
        self._observers: Dict[int, Observer] = {}
        self._handlers: Dict[int, _DebouncedHandler] = {}

    def start_watching(self, folder_id: int, folder_path: str) -> None:
        if folder_id in self._observers:
            logger.warning("Already watching folder %d: %s", folder_id, folder_path)
            return

        p = Path(folder_path)
        if not p.exists():
            logger.warning("Path does not exist, cannot watch: %s", folder_path)
            return

        watch_dir = folder_path if p.is_dir() else str(p.parent)

        handler = _DebouncedHandler(
            folder_id=folder_id,
            folder_path=folder_path,
            db=self.db,
            debounce_seconds=self.config.fs_debounce_seconds,
        )
        observer = Observer()
        observer.schedule(handler, watch_dir, recursive=p.is_dir())
        observer.daemon = True
        observer.start()

        self._observers[folder_id] = observer
        self._handlers[folder_id] = handler
        logger.info("Started watching: %s", folder_path)

    def stop_watching(self, folder_id: int) -> None:
        if folder_id in self._observers:
            self._observers[folder_id].stop()
            self._observers[folder_id].join(timeout=5)
            del self._observers[folder_id]
            del self._handlers[folder_id]
            logger.info("Stopped watching folder %d", folder_id)

    def stop_all(self) -> None:
        for fid in list(self._observers):
            self.stop_watching(fid)

    @property
    def watching(self) -> List[int]:
        return list(self._observers.keys())
