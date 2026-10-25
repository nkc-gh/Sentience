"""
SENTIENCE Configuration System.

XDG-compliant configuration following the XDG Base Directory Specification.
All paths follow:
  - Config:  $XDG_CONFIG_HOME/sentience/  (~/.config/sentience/)
  - Data:    $XDG_DATA_HOME/sentience/    (~/.local/share/sentience/)
  - State:   $XDG_STATE_HOME/sentience/   (~/.local/state/sentience/)
  - Cache:   $XDG_CACHE_HOME/sentience/   (~/.cache/sentience/)
  - Runtime: $XDG_RUNTIME_DIR/sentience/
"""

import os
import sys
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


def _xdg(env_var: str, fallback: str) -> Path:
    """Resolve an XDG base directory on Unix-like systems."""
    return Path(os.environ.get(env_var, Path.home() / fallback))


def _windows_base(name: str) -> Path:
    """Return a native Windows application-data directory."""
    if name == "config":
        root = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    else:
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    return Path(root) if root else Path.home() / "AppData" / "Local"


class SentienceConfig(BaseSettings):
    """Central configuration for SENTIENCE — XDG-compliant."""

    # ── Snapshot / tracking intervals ────────────────────
    snapshot_interval_seconds: int = 300
    memory_retention_days: int = 45
    fs_debounce_seconds: float = 2.0
    window_sample_interval_seconds: float = 1.0

    # ── Daemon ──────────────────────────────────────────
    daemon_socket_name: str = "sentience.sock"
    daemon_host: str = "127.0.0.1"
    daemon_port: int = 0  # 0 = use Unix socket on Unix or the default TCP port on Windows

    # ── Custom path overrides (e.g. for testing) ────────
    custom_data_dir: Optional[Path] = Field(default=None, alias="data_dir")
    custom_config_dir: Optional[Path] = Field(default=None, alias="config_dir")
    custom_state_dir: Optional[Path] = Field(default=None, alias="state_dir")
    custom_cache_dir: Optional[Path] = Field(default=None, alias="cache_dir")
    custom_runtime_dir: Optional[Path] = Field(default=None, alias="runtime_dir")
    custom_db_path: Optional[Path] = Field(default=None, alias="db_path")

    model_config = {
        "env_prefix": "SENTIENCE_",
        "extra": "allow",
        "populate_by_name": True,
    }

    # ── XDG directory properties ────────────────────────

    @property
    def config_dir(self) -> Path:
        """User configuration directory."""
        if self.custom_config_dir:
            return self.custom_config_dir
        if self.custom_data_dir:
            return self.custom_data_dir / "config"
        if sys.platform == "win32":
            return _windows_base("config") / "Sentience"
        return _xdg("XDG_CONFIG_HOME", ".config") / "sentience"

    @property
    def data_dir(self) -> Path:
        """Persistent application data directory."""
        if self.custom_data_dir:
            return self.custom_data_dir
        if sys.platform == "win32":
            return _windows_base("data") / "Sentience"
        return _xdg("XDG_DATA_HOME", ".local/share") / "sentience"

    @property
    def state_dir(self) -> Path:
        """$XDG_STATE_HOME/sentience/ — logs, migrations, task state."""
        if self.custom_state_dir:
            return self.custom_state_dir
        if self.custom_data_dir:
            return self.custom_data_dir / "state"
        if sys.platform == "win32":
            return _windows_base("data") / "Sentience" / "state"
        return _xdg("XDG_STATE_HOME", ".local/state") / "sentience"

    @property
    def cache_dir(self) -> Path:
        """$XDG_CACHE_HOME/sentience/ — temporary indexes, caches."""
        if self.custom_cache_dir:
            return self.custom_cache_dir
        if self.custom_data_dir:
            return self.custom_data_dir / "cache"
        if sys.platform == "win32":
            return _windows_base("data") / "Sentience" / "cache"
        return _xdg("XDG_CACHE_HOME", ".cache") / "sentience"

    @property
    def runtime_dir(self) -> Path:
        """$XDG_RUNTIME_DIR/sentience/ — socket, daemon lock."""
        if self.custom_runtime_dir:
            return self.custom_runtime_dir
        if self.custom_data_dir:
            return self.custom_data_dir / "runtime"
        if sys.platform == "win32":
            return _windows_base("data") / "Sentience" / "runtime"
        base = os.environ.get("XDG_RUNTIME_DIR")
        if base:
            return Path(base) / "sentience"
        # Fallback if XDG_RUNTIME_DIR is not set
        return Path("/tmp") / f"sentience-{os.getuid()}"

    # ── Derived paths ───────────────────────────────────

    @property
    def db_path(self) -> Path:
        if self.custom_db_path:
            return self.custom_db_path
        if (self.data_dir / "memory" / "sentience.db").exists():
            return self.data_dir / "memory" / "sentience.db"
        return self.data_dir / "sentience.db"

    @property
    def file_history_dir(self) -> Path:
        return self.data_dir / "file-history"

    @property
    def memory_dir(self) -> Path:
        return self.data_dir / "memory"

    @property
    def vector_index_dir(self) -> Path:
        return self.data_dir / "vector-index"

    @property
    def repos_dir(self) -> Path:
        return self.data_dir / "file-history" / "repositories"

    @property
    def policies_dir(self) -> Path:
        return self.config_dir / "policies"

    @property
    def log_dir(self) -> Path:
        return self.state_dir / "logs"

    @property
    def audit_dir(self) -> Path:
        return self.state_dir / "logs" / "audit"

    @property
    def migrations_dir(self) -> Path:
        return self.state_dir / "migrations"

    @property
    def task_state_dir(self) -> Path:
        return self.state_dir / "task-state"

    @property
    def socket_path(self) -> Path:
        return self.runtime_dir / self.daemon_socket_name

    @property
    def daemon_lock_path(self) -> Path:
        return self.runtime_dir / "daemon.lock"

    @property
    def skills_dir(self) -> Path:
        return self.config_dir / "skills"

    @property
    def hooks_dir(self) -> Path:
        return self.config_dir / "hooks"

    @property
    def shell_log_path(self) -> Path:
        return self.state_dir / "logs" / "shell_history.jsonl"

    @property
    def repl_history_path(self) -> Path:
        return self.state_dir / "repl_history.txt"

    @property
    def settings_path(self) -> Path:
        return self.config_dir / "settings.json"

    def get_setting(self, key: str, default: str = "") -> str:
        """Read a user setting from settings.json."""
        if self.settings_path.exists():
            try:
                import json
                data = json.loads(self.settings_path.read_text(encoding="utf-8"))
                return data.get(key, default)
            except Exception:
                pass
        return default

    def set_setting(self, key: str, value: str) -> None:
        """Persist a user setting to settings.json."""
        import json
        self.config_dir.mkdir(parents=True, exist_ok=True)
        data = {}
        if self.settings_path.exists():
            try:
                data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data[key] = value
        self.settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @property
    def preferred_editor(self) -> str:
        """Get user configured editor, fallback to $EDITOR, then system default."""
        custom = self.get_setting("preferred_editor", "").strip()
        if custom:
            return custom
        default = "notepad.exe" if sys.platform == "win32" else "nano"
        return os.environ.get("EDITOR", default)
