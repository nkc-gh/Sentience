"""
SENTIENCE — Phase 1 & 2 Test Suite.

Tests cover:
  • Configuration & storage initialisation
  • Database CRUD for all five tables
  • Tracked-folder management
  • Filesystem collector (ignore rules + live watchdog detection)
  • Shadow Git manager (init / snapshot / diff / history)
  • Document text extraction
  • Shell integration (hook generation + JSONL parsing)
  • Memory query interface (timeline / search / recent_changes / etc.)
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from config import SentienceConfig
from memory.database import MemoryDatabase
from memory.documents import extract_text, is_text_file
from memory.filesystem import FilesystemCollector, _should_ignore
from memory.git_history import ShadowGitManager
from memory.queries import MemoryQuery
from memory.shell import ShellIntegration
from memory.tracker import TrackedFolderManager
from models import (
    FileEvent,
    FileEventType,
    GitSnapshot,
    PrivacyLabel,
    ShellCommand,
    TrackedFolder,
    TrackingStatus,
    WindowSession,
)
from storage import initialize_storage


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture()
def config(tmp_path):
    return SentienceConfig(data_dir=tmp_path / ".sentience")


@pytest.fixture()
def db(config):
    initialize_storage(config)
    database = MemoryDatabase(config.db_path)
    yield database
    database.close()


@pytest.fixture()
def tracker(db):
    return TrackedFolderManager(db)


@pytest.fixture()
def git(config):
    initialize_storage(config)
    return ShadowGitManager(config)


# ═══════════════════════════════════════════════════════════
# Config & Storage
# ═══════════════════════════════════════════════════════════


class TestConfig:
    def test_default_paths(self):
        import os
        cfg = SentienceConfig()
        expected_data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "sentience"
        assert cfg.data_dir == expected_data
        assert cfg.db_path == expected_data / "sentience.db"

    def test_custom_data_dir(self, tmp_path):
        cfg = SentienceConfig(data_dir=tmp_path / "custom")
        assert cfg.memory_dir == tmp_path / "custom" / "memory"



class TestStorage:
    def test_creates_directories(self, config):
        initialize_storage(config)
        for d in (
            config.data_dir,
            config.memory_dir,
            config.repos_dir,
            config.audit_dir,
            config.skills_dir,
            config.config_dir,
            config.hooks_dir,
        ):
            assert d.exists(), f"Missing: {d}"

    def test_idempotent(self, config):
        initialize_storage(config)
        initialize_storage(config)
        assert config.data_dir.exists()


# ═══════════════════════════════════════════════════════════
# Database CRUD
# ═══════════════════════════════════════════════════════════


class TestDatabase:
    def test_add_get_folder(self, db):
        folder = db.add_folder(TrackedFolder(path="/tmp/test_proj"))
        assert folder.id is not None
        got = db.get_folder(folder.id)
        assert got is not None
        assert got.path == "/tmp/test_proj"
        assert got.label == PrivacyLabel.PUBLIC

    def test_get_folder_by_path(self, db):
        db.add_folder(TrackedFolder(path="/tmp/by_path"))
        assert db.get_folder_by_path("/tmp/by_path") is not None

    def test_duplicate_raises(self, db):
        db.add_folder(TrackedFolder(path="/tmp/dup"))
        with pytest.raises(Exception):
            db.add_folder(TrackedFolder(path="/tmp/dup"))

    def test_update_status(self, db):
        f = db.add_folder(TrackedFolder(path="/tmp/status"))
        db.update_folder_status(f.id, TrackingStatus.PAUSED)
        assert db.get_folder(f.id).status == TrackingStatus.PAUSED

    def test_update_label(self, db):
        f = db.add_folder(TrackedFolder(path="/tmp/label"))
        db.update_folder_label(f.id, PrivacyLabel.PRIVATE)
        assert db.get_folder(f.id).label == PrivacyLabel.PRIVATE

    def test_remove_folder(self, db):
        f = db.add_folder(TrackedFolder(path="/tmp/rm"))
        db.remove_folder(f.id)
        assert db.get_folder(f.id) is None

    def test_active_folders(self, db):
        db.add_folder(TrackedFolder(path="/tmp/a1"))
        f2 = db.add_folder(TrackedFolder(path="/tmp/a2"))
        db.update_folder_status(f2.id, TrackingStatus.PAUSED)
        active = db.get_active_folders()
        assert len(active) == 1
        assert active[0].path == "/tmp/a1"

    def test_file_event(self, db):
        f = db.add_folder(TrackedFolder(path="/tmp/fe"))
        ev = db.add_file_event(
            FileEvent(
                folder_id=f.id,
                event_type=FileEventType.MODIFIED,
                file_path="src/main.py",
                lines_added=10,
                lines_removed=3,
            )
        )
        assert ev.id is not None
        rows = db.get_file_events(folder_id=f.id)
        assert len(rows) == 1
        assert rows[0].lines_added == 10

    def test_file_events_time_filter(self, db):
        f = db.add_folder(TrackedFolder(path="/tmp/tf"))
        db.add_file_event(
            FileEvent(
                folder_id=f.id,
                event_type=FileEventType.CREATED,
                file_path="old.py",
                timestamp=datetime.now() - timedelta(days=5),
            )
        )
        db.add_file_event(
            FileEvent(
                folder_id=f.id,
                event_type=FileEventType.CREATED,
                file_path="new.py",
            )
        )
        recent = db.get_file_events(
            folder_id=f.id, since=datetime.now() - timedelta(hours=1)
        )
        assert len(recent) == 1
        assert recent[0].file_path == "new.py"

    def test_window_session(self, db):
        now = datetime.now()
        s = db.add_window_session(
            WindowSession(
                app_name="code",
                window_title="main.py — VS Code",
                start_time=now,
                end_time=now + timedelta(minutes=30),
                duration_seconds=1800,
            )
        )
        assert s.id is not None
        rows = db.get_window_sessions()
        assert len(rows) == 1
        assert rows[0].duration_seconds == 1800
        assert rows[0].time_str is not None
        assert "AM" in rows[0].time_str or "PM" in rows[0].time_str

    def test_shell_command(self, db):
        c = db.add_shell_command(
            ShellCommand(
                shell_type="bash",
                command="python main.py",
                working_directory="/home/user/proj",
                exit_code=0,
                duration_seconds=1.5,
            )
        )
        assert c.id is not None
        rows = db.get_shell_commands()
        assert rows[0].command == "python main.py"

    def test_git_snapshot(self, db):
        f = db.add_folder(TrackedFolder(path="/tmp/gs"))
        snap = db.add_git_snapshot(
            GitSnapshot(
                folder_id=f.id,
                commit_hash="abc123",
                message="snap",
                files_changed=5,
                lines_added=100,
                lines_removed=20,
            )
        )
        assert snap.id is not None
        rows = db.get_git_snapshots(folder_id=f.id)
        assert rows[0].files_changed == 5


# ═══════════════════════════════════════════════════════════
# Tracked Folder Manager
# ═══════════════════════════════════════════════════════════


class TestTracker:
    def test_add(self, tracker, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        f = tracker.add(str(proj))
        assert f.id is not None
        assert f.status == TrackingStatus.ACTIVE

    def test_nonexistent_raises(self, tracker):
        with pytest.raises(FileNotFoundError):
            tracker.add("/does/not/exist/xyz")

    def test_duplicate_raises(self, tracker, tmp_path):
        proj = tmp_path / "dup_p"
        proj.mkdir()
        tracker.add(str(proj))
        with pytest.raises(ValueError):
            tracker.add(str(proj))

    def test_pause_resume(self, tracker, tmp_path):
        proj = tmp_path / "pr"
        proj.mkdir()
        f = tracker.add(str(proj))
        tracker.pause(f.id)
        assert tracker.get(f.id).status == TrackingStatus.PAUSED
        tracker.resume(f.id)
        assert tracker.get(f.id).status == TrackingStatus.ACTIVE

    def test_set_label(self, tracker, tmp_path):
        proj = tmp_path / "lbl"
        proj.mkdir()
        f = tracker.add(str(proj))
        tracker.set_label(f.id, PrivacyLabel.PRIVATE)
        assert tracker.get(f.id).label == PrivacyLabel.PRIVATE

    def test_find_folder_for_path(self, tracker, tmp_path):
        proj = tmp_path / "find"
        proj.mkdir()
        (proj / "src").mkdir()
        f = tracker.add(str(proj))
        found = tracker.find_folder_for_path(str(proj / "src" / "main.py"))
        assert found is not None
        assert found.id == f.id

    def test_find_returns_none_for_untracked(self, tracker):
        assert tracker.find_folder_for_path("/untracked/a.py") is None


# ═══════════════════════════════════════════════════════════
# Filesystem Collector
# ═══════════════════════════════════════════════════════════


class TestFilesystem:
    def test_ignore_rules(self):
        assert _should_ignore("/p/.git/objects/abc")
        assert _should_ignore("/p/__pycache__/mod.pyc")
        assert _should_ignore("/p/node_modules/pkg/i.js")
        assert _should_ignore("/p/file.pyc")
        assert _should_ignore("/p/.#lock")
        assert not _should_ignore("/p/src/main.py")
        assert not _should_ignore("/p/README.md")

    def test_start_stop(self, db, config, tmp_path):
        proj = tmp_path / "watch"
        proj.mkdir()
        f = db.add_folder(TrackedFolder(path=str(proj)))
        col = FilesystemCollector(db, config)
        col.start_watching(f.id, str(proj))
        assert f.id in col.watching
        col.stop_watching(f.id)
        assert f.id not in col.watching

    def test_detects_creation(self, db, config, tmp_path):
        """Live test: create a file and verify the collector records it."""
        proj = tmp_path / "live"
        proj.mkdir()
        f = db.add_folder(TrackedFolder(path=str(proj)))
        config.fs_debounce_seconds = 0.5
        col = FilesystemCollector(db, config)
        col.start_watching(f.id, str(proj))

        (proj / "hello.py").write_text("print('hi')")
        time.sleep(2.0)

        col.stop_all()
        events = db.get_file_events(folder_id=f.id)
        types = {e.event_type for e in events}
        assert FileEventType.CREATED in types or FileEventType.MODIFIED in types


# ═══════════════════════════════════════════════════════════
# Shadow Git
# ═══════════════════════════════════════════════════════════


class TestGitHistory:
    def test_init_repo(self, git, tmp_path):
        proj = tmp_path / "git_p"
        proj.mkdir()
        repo = git.init_repo(str(proj))
        assert (repo / ".git").exists()

    def test_snapshot(self, git, tmp_path):
        proj = tmp_path / "snap"
        proj.mkdir()
        (proj / "main.py").write_text("print('hello')")
        git.init_repo(str(proj))
        result = git.snapshot(str(proj), "first")
        assert result is not None
        assert result["commit_hash"]
        assert result["files_changed"] >= 1

    def test_no_change_returns_none(self, git, tmp_path):
        proj = tmp_path / "noop"
        proj.mkdir()
        (proj / "a.py").write_text("x = 1")
        git.init_repo(str(proj))
        git.snapshot(str(proj))
        assert git.snapshot(str(proj)) is None

    def test_diff(self, git, tmp_path):
        proj = tmp_path / "diff_p"
        proj.mkdir()
        (proj / "a.py").write_text("v1")
        git.init_repo(str(proj))
        git.snapshot(str(proj), "v1")
        (proj / "a.py").write_text("v2\nextra")
        git.snapshot(str(proj), "v2")
        d = git.diff(str(proj))
        assert "v1" in d or "v2" in d

    def test_file_history(self, git, tmp_path):
        import os

        proj = tmp_path / "fhist"
        proj.mkdir()
        git.init_repo(str(proj))
        (proj / "app.py").write_text("v1")
        git.snapshot(str(proj), "v1")
        (proj / "app.py").write_text("v2")
        # Force a newer mtime so rsync detects the change
        future = time.time() + 2
        os.utime(proj / "app.py", (future, future))
        git.snapshot(str(proj), "v2")
        hist = git.file_history(str(proj), "app.py")
        assert len(hist) >= 2


# ═══════════════════════════════════════════════════════════
# Documents
# ═══════════════════════════════════════════════════════════


class TestDocuments:
    def test_text_file(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("print('hello')")
        assert extract_text(str(f)) == "print('hello')"

    def test_markdown(self, tmp_path):
        f = tmp_path / "readme.md"
        f.write_text("# Hello\nWorld")
        assert "# Hello" in extract_text(str(f))

    def test_nonexistent(self):
        assert extract_text("/nonexistent/file.py") is None

    def test_is_text(self):
        assert is_text_file("main.py")
        assert is_text_file("data.json")
        assert not is_text_file("image.png")


# ═══════════════════════════════════════════════════════════
# Shell Integration
# ═══════════════════════════════════════════════════════════


class TestShell:
    def test_install_hooks(self, db, config):
        initialize_storage(config)
        shell = ShellIntegration(db, config)
        paths = shell.install_hooks()
        assert "bash" in paths and "zsh" in paths and "fish" in paths
        assert Path(paths["bash"]).exists()

    def test_process_line(self, db, config):
        initialize_storage(config)
        shell = ShellIntegration(db, config)
        line = json.dumps(
            {
                "shell": "bash",
                "command": "python test.py",
                "cwd": "/home/user/proj",
                "exit_code": 0,
                "timestamp": datetime.now().isoformat(),
                "duration_ms": 1500,
            }
        )
        shell._process_line(line)
        cmds = db.get_shell_commands()
        assert len(cmds) == 1
        assert cmds[0].command == "python test.py"
        assert cmds[0].exit_code == 0


# ═══════════════════════════════════════════════════════════
# Memory Queries
# ═══════════════════════════════════════════════════════════


class TestMemoryQuery:
    def test_timeline(self, db, git):
        f = db.add_folder(TrackedFolder(path="/tmp/q_tl"))
        db.add_file_event(
            FileEvent(
                folder_id=f.id,
                event_type=FileEventType.MODIFIED,
                file_path="main.py",
                lines_added=10,
            )
        )
        db.add_window_session(
            WindowSession(
                app_name="code",
                window_title="main.py",
                start_time=datetime.now(),
                end_time=datetime.now(),
                duration_seconds=300,
            )
        )
        q = MemoryQuery(db, git)
        result = q.timeline(hours=1)
        assert result["total_events"] >= 2

    def test_search(self, db, git):
        f = db.add_folder(TrackedFolder(path="/tmp/q_sr"))
        db.add_file_event(
            FileEvent(
                folder_id=f.id,
                event_type=FileEventType.CREATED,
                file_path="auth.py",
            )
        )
        db.add_file_event(
            FileEvent(
                folder_id=f.id,
                event_type=FileEventType.MODIFIED,
                file_path="utils.py",
            )
        )
        result = MemoryQuery(db, git).search("auth")
        assert result["total_matches"] >= 1

    def test_recent_changes(self, db, git):
        f = db.add_folder(TrackedFolder(path="/tmp/q_rc"))
        db.add_file_event(
            FileEvent(
                folder_id=f.id,
                event_type=FileEventType.MODIFIED,
                file_path="app.py",
                lines_added=20,
                lines_removed=5,
            )
        )
        result = MemoryQuery(db, git).recent_changes(hours=1)
        assert result["files_changed"] == 1
        assert result["files"][0]["total_lines_added"] == 20

    def test_recently_deleted(self, db, git):
        f = db.add_folder(TrackedFolder(path="/tmp/q_del"))
        db.add_file_event(
            FileEvent(
                folder_id=f.id,
                event_type=FileEventType.DELETED,
                file_path="old.py",
            )
        )
        deleted = MemoryQuery(db, git).recently_deleted(hours=1)
        assert len(deleted) == 1
        assert deleted[0]["path"] == "old.py"

    def test_window_history(self, db, git):
        now = datetime.now()
        db.add_window_session(
            WindowSession(
                app_name="firefox",
                window_title="Google",
                start_time=now,
                end_time=now + timedelta(minutes=10),
                duration_seconds=600,
            )
        )
        result = MemoryQuery(db, git).window_history(hours=1)
        assert result["total_sessions"] == 1
        assert result["app_summary"][0]["app"] == "firefox"

    def test_shell_history(self, db, git):
        db.add_shell_command(
            ShellCommand(
                shell_type="zsh",
                command="pytest -v",
                working_directory="/home/user/proj",
                exit_code=0,
            )
        )
        result = MemoryQuery(db, git).shell_history(hours=1)
        assert result["total_commands"] == 1
        assert result["commands"][0]["command"] == "pytest -v"
