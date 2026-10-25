"""
Tests for WindowTracker and window monitoring.
"""

import os
import time
from datetime import datetime
from memory.database import MemoryDatabase
from memory.windows import WindowTracker, get_active_window


def test_mock_window_override(monkeypatch):
    monkeypatch.setenv("SENTIENCE_MOCK_WINDOW", "VSCode|||main.py — Sentience")
    active = get_active_window()
    assert active == ("VSCode", "main.py — Sentience")


def test_window_tracker_session_lifecycle(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    db = MemoryDatabase(str(db_path))

    # Mock window 1
    monkeypatch.setenv("SENTIENCE_MOCK_WINDOW", "Code|||editor.py")

    tracker = WindowTracker(db, sample_interval=0.1)
    tracker._sample()

    sessions = db.get_window_sessions()
    assert len(sessions) == 1
    assert sessions[0].app_name == "Code"
    assert sessions[0].window_title == "editor.py"

    # Same window again: should update/extend the same session, not create a new one
    time.sleep(0.05)
    tracker._sample()
    sessions = db.get_window_sessions()
    assert len(sessions) == 1

    # Switch window: should finalise old and create new
    monkeypatch.setenv("SENTIENCE_MOCK_WINDOW", "Terminal|||bash")
    tracker._sample()
    sessions = db.get_window_sessions()
    assert len(sessions) == 2
    assert sessions[0].app_name == "Terminal"
    assert sessions[1].app_name == "Code"

    # Stop tracker (finalises)
    tracker.stop()


def test_wayland_does_not_fallback_without_gnome_access(monkeypatch):
    monkeypatch.delenv("SENTIENCE_MOCK_WINDOW", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr("computer.window_tracker._get_active_window_gnome", lambda: None)
    monkeypatch.setattr(
        "computer.window_tracker._get_active_window_x11",
        lambda: ("Chatgpt", "ChatGPT"),
    )
    assert get_active_window() is None
