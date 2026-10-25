"""Windows-only platform regression tests for the Sentience fork."""

import importlib
import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows platform tests")


def test_windows_config_uses_native_application_data(tmp_path, monkeypatch):
    from config import SentienceConfig

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "RoamingAppData"))
    config = SentienceConfig()

    assert config.data_dir == tmp_path / "LocalAppData" / "Sentience"
    assert config.config_dir == tmp_path / "RoamingAppData" / "Sentience"
    assert config.runtime_dir == tmp_path / "LocalAppData" / "Sentience" / "runtime"
    assert config.preferred_editor == "notepad.exe"


def test_all_python_modules_import_without_posix_only_import_errors():
    """Import every package module to catch accidental fcntl/pty imports."""
    import pkgutil
    import sys

    source_root = Path(__file__).parents[1] / "src"
    sys.path.insert(0, str(source_root))
    failures = []
    for module in pkgutil.walk_packages([str(source_root)]):
        if module.name.startswith("tests"):
            continue
        try:
            importlib.import_module(module.name)
        except Exception as exc:  # pragma: no cover - assertion output is the test
            failures.append(f"{module.name}: {type(exc).__name__}: {exc}")
    assert failures == []


def test_windows_foreground_window_provider_returns_safe_tuple_or_none():
    from computer.window_tracker import get_active_window

    result = get_active_window()
    assert result is None or (
        isinstance(result, tuple)
        and len(result) == 2
        and all(isinstance(value, str) for value in result)
    )


def test_windows_notepad_session_is_correlated_to_tracked_file():
    from datetime import datetime, timedelta
    from types import SimpleNamespace

    from computer.window_correlation import match_file_editor_sessions

    start = datetime(2026, 10, 22, 19, 38, 41)
    session = SimpleNamespace(
        app_name="Notepad.exe",
        window_title="1.c.txt - Notepad",
        start_time=start,
        end_time=start + timedelta(seconds=5),
        duration_seconds=5.0,
    )
    match = match_file_editor_sessions(
        "workspace/1.c.txt",
        start + timedelta(seconds=2),
        [session],
    )
    assert match is not None
    assert match["apps"] == ["Notepad.exe"]


def test_any_window_with_filename_in_title_is_correlated():
    from datetime import datetime, timedelta
    from types import SimpleNamespace

    from computer.window_correlation import matching_file_window_sessions

    start = datetime(2026, 10, 22, 19, 38, 41)
    session = SimpleNamespace(
        app_name="CustomTool.exe",
        window_title="1.c.txt - Custom Tool",
        start_time=start,
        end_time=start + timedelta(seconds=5),
        duration_seconds=5.0,
    )
    assert matching_file_window_sessions("workspace/1.c.txt", [session]) == [session]


def test_powershell_hook_is_installed_and_has_json_logging(tmp_path):
    from computer.shell_integration import ShellIntegration
    from config import SentienceConfig
    from storage.database import Database

    config = SentienceConfig(data_dir=tmp_path / "data", config_dir=tmp_path / "config")
    db = Database(config.db_path)
    try:
        integration = ShellIntegration(db, config)
        paths = integration.install_hooks()
        hook = Path(paths["powershell"])
        content = hook.read_text(encoding="utf-8")
        assert hook.suffix == ".ps1"
        assert "ConvertTo-Json" in content
        assert "Add-Content" in content
        assert str(config.shell_log_path) in content
        assert ".ps1" in integration.get_source_instructions()
    finally:
        db.close()


def test_windows_terminal_resolves_an_installed_native_shell(tmp_path):
    from cli.commands.terminal import _configured_shell
    from config import SentienceConfig

    shell = MagicMock()
    shell.config = SentienceConfig(config_dir=tmp_path / "config")
    name, path = _configured_shell(shell)
    assert name.casefold() in {"powershell", "pwsh", "cmd"}
    assert path.is_file()


def test_windows_terminal_dispatch_uses_native_subprocess(tmp_path, monkeypatch):
    from cli.commands import terminal

    shell = MagicMock()
    shell.db.add_shell_command = MagicMock()
    calls = []
    monkeypatch.setattr(terminal.subprocess, "call", lambda *args, **kwargs: calls.append((args, kwargs)) or 0)
    terminal._windows_terminal_command(shell, "powershell", Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"))
    assert calls
    assert calls[0][0][0][0].lower().endswith("powershell.exe")
    shell.db.add_shell_command.assert_called_once()


def test_windows_git_history_finds_git_even_if_path_is_missing(tmp_path, monkeypatch):
    from computer.git_history import ShadowGitManager
    from config import SentienceConfig

    git_path = shutil.which("git") or Path("C:/Program Files/Git/cmd/git.exe")
    if not Path(git_path).is_file():
        pytest.skip("Git for Windows is not installed")
    monkeypatch.setenv("PATH", "")
    assert Path(ShadowGitManager._git_executable()).resolve() == Path(git_path).resolve()


def test_windows_daemon_selects_localhost_tcp(monkeypatch, tmp_path):
    import daemon.server as daemon_server
    from config import SentienceConfig
    from storage.database import Database

    config = SentienceConfig(data_dir=tmp_path / "data", config_dir=tmp_path / "config")
    db = Database(config.db_path)
    captured = {}

    class FakeServer:
        def __init__(self, server_config):
            captured["config"] = server_config

        def run(self):
            captured["ran"] = True

    monkeypatch.setattr(daemon_server.uvicorn, "Server", FakeServer)
    try:
        daemon_server.serve(config, db)
    finally:
        db.close()
    assert captured["ran"] is True
    assert captured["config"].host == "127.0.0.1"
    assert captured["config"].port == 8765
    assert captured["config"].uds is None

