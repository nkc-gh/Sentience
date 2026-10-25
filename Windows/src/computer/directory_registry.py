"""
Tracked Folder Manager.

Manages the set of folders the user has opted-in for SENTIENCE to
monitor.  Handles add / remove / pause / resume / label operations
and maps arbitrary file paths back to their owning tracked folder.
"""

import logging
from pathlib import Path
from typing import List, Optional

from storage.database import Database
from storage.models import PrivacyLabel, TrackedFolder, TrackingStatus

logger = logging.getLogger(__name__)


class TrackedFolderManager:
    """CRUD controller for tracked folders."""

    def __init__(self, db: Database):
        self.db = db

    # ── Add / Remove ─────────────────────────────────────

    def add(
        self, path: str, label: PrivacyLabel = PrivacyLabel.PUBLIC
    ) -> TrackedFolder:
        """Register a folder for tracking.

        Raises:
            FileNotFoundError: if *path* does not exist on disk.
            ValueError:        if *path* is already tracked.
        """
        resolved = str(Path(path).expanduser().resolve())

        if not Path(resolved).is_dir():
            raise FileNotFoundError(f"Directory does not exist: {resolved}")

        existing = self.db.get_folder_by_path(resolved)
        if existing:
            raise ValueError(f"Folder already tracked: {resolved}")

        folder = TrackedFolder(path=resolved, label=label)
        saved = self.db.add_folder(folder)
        logger.info("Registered tracked folder: %s [%s]", resolved, label.value)
        return saved

    def remove(self, folder_id: int) -> None:
        """Un-track a folder and delete all its stored history."""
        self.db.remove_folder(folder_id)
        logger.info("Removed tracked folder %d", folder_id)

    # ── Status management ────────────────────────────────

    def pause(self, folder_id: int) -> None:
        self.db.update_folder_status(folder_id, TrackingStatus.PAUSED)
        logger.info("Paused folder %d", folder_id)

    def resume(self, folder_id: int) -> None:
        self.db.update_folder_status(folder_id, TrackingStatus.ACTIVE)
        logger.info("Resumed folder %d", folder_id)

    # ── Privacy label ────────────────────────────────────

    def set_label(self, folder_id: int, label: PrivacyLabel) -> None:
        self.db.update_folder_label(folder_id, label)
        logger.info("Set folder %d label → %s", folder_id, label.value)

    # ── History ──────────────────────────────────────────

    def delete_history(self, folder_id: int) -> None:
        """Delete stored events/snapshots without un-tracking the folder."""
        self.db.delete_folder_history(folder_id)
        logger.info("Deleted history for folder %d", folder_id)

    # ── Queries ──────────────────────────────────────────

    def get_all(self) -> List[TrackedFolder]:
        return self.db.get_all_folders()

    def get_active(self) -> List[TrackedFolder]:
        return self.db.get_active_folders()

    def get(self, folder_id: int) -> Optional[TrackedFolder]:
        return self.db.get_folder(folder_id)

    def find_folder_for_path(self, file_path: str) -> Optional[TrackedFolder]:
        """Given an absolute file path, return the most-specific tracked
        folder that contains it (or ``None``)."""
        resolved = str(Path(file_path).resolve())
        folders = self.db.get_all_folders()
        best: Optional[TrackedFolder] = None
        for f in folders:
            try:
                Path(resolved).relative_to(Path(f.path).resolve())
                contained = True
            except (ValueError, OSError):
                contained = False
            if contained:
                if best is None or len(f.path) > len(best.path):
                    best = f
        return best
