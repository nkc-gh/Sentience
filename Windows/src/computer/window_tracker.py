"""
Window / Application Activity Tracker.

Samples the active window every N seconds via GNOME Shell's DBus
interface (works on Wayland).  Consecutive samples for the same
application + title are aggregated into a single ``WindowSession``
with accurate start/end timestamps.
"""

import ctypes
import logging
import os
import subprocess
import threading
import time
from datetime import datetime
from typing import Optional, Tuple

from storage.database import Database
from storage.models import WindowSession

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# Active-window detection (GNOME / Wayland)
# ═══════════════════════════════════════════════════════════


def _get_active_window_windows() -> Optional[Tuple[str, str]]:
    """Return the foreground Windows application and title through User32."""
    if os.name != "nt":
        return None
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        length = user32.GetWindowTextLengthW(hwnd)
        title_buffer = ctypes.create_unicode_buffer(max(1, length + 1))
        user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
        title = title_buffer.value.strip() or "Untitled"

        process_id = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        app_name = f"PID {process_id.value}" if process_id.value else "Unknown"
        if process_id.value:
            try:
                import psutil

                app_name = psutil.Process(process_id.value).name() or app_name
            except Exception:
                pass
        return app_name, title
    except Exception as exc:
        logger.debug("Windows foreground-window detection failed: %s", exc)
        return None


def _get_active_window_gnome() -> Optional[Tuple[str, str]]:
    """Query GNOME Shell via DBus for the focused window.

    Returns ``(app_name, window_title)`` or ``None``.
    """
    js = (
        "const w = global.display.focus_window; "
        "w ? w.get_wm_class() + '|||' + w.get_title() : ''"
    )
    try:
        result = subprocess.run(
            [
                "gdbus", "call", "--session",
                "--dest", "org.gnome.Shell",
                "--object-path", "/org/gnome/Shell",
                "--method", "org.gnome.Shell.Eval",
                js,
            ],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0:
            return None

        out = result.stdout.strip()
        # Output looks like: (true, 'Alacritty|||~')
        if "''" in out or out.endswith("''"):
            return None

        start = out.find("'") + 1
        end = out.rfind("'")
        if start <= 0 or end <= start:
            return None

        value = out[start:end]
        if "|||" not in value:
            return None

        app, title = value.split("|||", 1)
        return (app.strip() or "Unknown", title.strip() or "Untitled")

    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as exc:
        logger.debug("GNOME window detection failed: %s", exc)
        return None


def _get_active_window_x11() -> Optional[Tuple[str, str]]:
    """Query X11 / Xwayland via xprop for the focused window.

    Returns ``(app_name, window_title)`` or ``None``.
    """
    try:
        root_res = subprocess.run(
            ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if root_res.returncode != 0 or not root_res.stdout:
            return None

        # Example: _NET_ACTIVE_WINDOW(WINDOW): window id # 0x400003
        parts = root_res.stdout.strip().split()
        if not parts:
            return None
        win_id = parts[-1]
        if not win_id.startswith("0x") or win_id in ("0x0", "0x00"):
            return None

        prop_res = subprocess.run(
            ["xprop", "-id", win_id, "WM_CLASS", "_NET_WM_NAME"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if prop_res.returncode != 0 or not prop_res.stdout:
            return None

        app_name = "Unknown"
        window_title = "Untitled"
        for line in prop_res.stdout.splitlines():
            if "WM_CLASS" in line and "=" in line:
                val = line.split("=", 1)[1].strip()
                items = [x.strip(' "') for x in val.split(",")]
                if len(items) > 1 and items[1]:
                    app_name = items[1]
                elif items and items[0]:
                    app_name = items[0]
            elif "_NET_WM_NAME" in line and "=" in line:
                val = line.split("=", 1)[1].strip().strip('"')
                if val:
                    window_title = val

        if app_name == "Unknown" and window_title == "Untitled":
            return None
        return (app_name, window_title)
    except Exception as exc:
        logger.debug("X11 window detection failed: %s", exc)
        return None


def get_active_window() -> Optional[Tuple[str, str]]:
    """Detect the currently focused window.

    Checks:
    1. Environment mock override ``SENTIENCE_MOCK_WINDOW="AppName|||Title"`` (for testing)
    2. GNOME Shell DBus interface (GNOME / Wayland)
    3. xprop (X11 / Xwayland)
    """
    mock = os.environ.get("SENTIENCE_MOCK_WINDOW")
    if mock:
        if "|||" in mock:
            parts = mock.split("|||", 1)
            return (parts[0].strip(), parts[1].strip())
        return (mock.strip(), "Active Window")

    if os.name == "nt":
        return _get_active_window_windows()

    # Try GNOME Shell DBus. On GNOME Wayland this is the permission-gated
    # path; if Looking Glass unsafe mode is off, it returns no window data.
    info = _get_active_window_gnome()
    if info:
        return info

    # Do not fall back to X11/Xwayland on a Wayland session. That fallback can
    # bypass the user's explicit GNOME unsafe-mode choice and start recording
    # windows even though the permission-gated API is unavailable.
    if os.environ.get("WAYLAND_DISPLAY"):
        return None

    # Fallback to X11 / Xwayland via xprop
    return _get_active_window_x11()


# ═══════════════════════════════════════════════════════════
# Tracker
# ═══════════════════════════════════════════════════════════


class WindowTracker:
    """Periodically samples the active window and writes aggregated
    sessions to the database."""

    def __init__(
        self,
        db: Database,
        sample_interval: float = 5.0,
    ):
        self.db = db
        self.sample_interval = sample_interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._current_session: Optional[WindowSession] = None
        self._current_session_id: Optional[int] = None
        self._session_lock = threading.RLock()

    # ── Lifecycle ────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info(
            "Window tracker started (sampling every %.1fs)", self.sample_interval
        )

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=self.sample_interval + 2)
        self._finalise_session()
        logger.info("Window tracker stopped")

    # ── Internal ─────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            try:
                self._sample()
            except Exception:
                logger.exception("Window sampling error")
            time.sleep(self.sample_interval)

    def _sample(self) -> None:
        with self._session_lock:
            self._sample_current_window()

    def _sample_current_window(self) -> None:
        info = get_active_window()
        if info is None:
            return

        app_name, window_title = info
        now = datetime.now()

        # Same window as last sample → just extend the session.
        if (
            self._current_session is not None
            and self._current_session.app_name == app_name
            and self._current_session.window_title == window_title
        ):
            if self._current_session_id is not None:
                dur = (now - self._current_session.start_time).total_seconds()
                self.db.update_window_session_end(
                    self._current_session_id, now, dur
                )
            return

        # Different window → close old session, open new one.
        self._finalise_session()

        session = WindowSession(
            app_name=app_name,
            window_title=window_title,
            start_time=now,
            end_time=now,
            duration_seconds=0.0,
        )
        saved = self.db.add_window_session(session)
        self._current_session = session
        self._current_session_id = saved.id

    def finalize_current_session(self) -> None:
        """Close the current sample before its history row is removed."""
        self._finalise_session()

    def _finalise_session(self) -> None:
        with self._session_lock:
            if self._current_session and self._current_session_id:
                now = datetime.now()
                dur = (now - self._current_session.start_time).total_seconds()
                self.db.update_window_session_end(
                    self._current_session_id, now, dur
                )
            self._current_session = None
            self._current_session_id = None
