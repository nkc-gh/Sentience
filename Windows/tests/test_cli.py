"""
Comprehensive tests for SENTIENCE CLI, Command Palette, Tab Completer, and Core Systems.
"""

from datetime import datetime, timedelta
import io
from pathlib import Path
from unittest.mock import patch, MagicMock
from prompt_toolkit.document import Document
from rich.console import Console

from cli.shell import COMMAND_META, SentienceCompleter, SentienceShell, render_welcome_card, MOTTO
from cli.commands.archive import (
    PERMISSIONS_HELP_DATA,
    render_permissions_help_table,
    format_duration,
    format_access_badges,
    build_archive_table,
    get_path_item_type,
)
from cli.commands.chats import render_chats_table
from cli.commands.agent import render_continue_table
from cli.commands.model import mask_key as model_mask_key, discover_models_for_provider
from cli.commands.connector import CATALOG, mask_key as conn_mask_key
from cli.commands.boss import get_boss_rules_file, render_boss_table
from cli.commands.window import parse_time_input, format_session_time
from storage.models import TrackedFolder, PrivacyLabel, WindowSession, FileEvent, FileEventType, Chat, ModelRecord
from routing.registry import LLMModel, ProviderManager, KNOWN_MODELS
from skills.registry import SkillsManager


def test_command_meta_has_commands():
    """Verify COMMAND_META has registered core commands including settings, terminal, and argus."""
    expected = [
        "help", "agent", "chats", "archive", "argus", "window",
        "model", "routing", "sentinel", "connector", "skills", "boss",
        "settings", "terminal", "clear", "back", "home", "exit"
    ]
    assert list(COMMAND_META.keys()) == expected
    assert len(COMMAND_META) == len(expected)


def test_tab_completer_always_returns_all_commands_on_empty():
    """Verify Tab completer yields all commands on empty buffer and repeatedly."""
    completer = SentienceCompleter(COMMAND_META)

    # 1. At start: empty text
    doc_empty = Document("")
    completions = list(completer.get_completions(doc_empty, None))
    assert len(completions) == len(COMMAND_META)
    cmd_names = [c.text for c in completions]
    assert "help" in cmd_names
    assert "agent" in cmd_names
    assert "archive" in cmd_names
    assert "argus" in cmd_names
    assert "settings" in cmd_names
    assert "terminal" in cmd_names
    assert "exit" in cmd_names
    assert "back" in cmd_names
    assert "home" in cmd_names

    # 2. After running commands: empty buffer again
    doc_after_command = Document("   ")
    completions2 = list(completer.get_completions(doc_after_command, None))
    assert len(completions2) == len(COMMAND_META)

    # 3. Partial typing matches prefix
    doc_partial = Document("ag")
    completions_partial = list(completer.get_completions(doc_partial, None))
    assert len(completions_partial) == 1
    assert completions_partial[0].text == "agent"

    doc_partial_ar = Document("ar")
    completions_ar = list(completer.get_completions(doc_partial_ar, None))
    assert len(completions_ar) == 2
    assert "archive" in [c.text for c in completions_ar]
    assert "argus" in [c.text for c in completions_ar]


def test_archive_permissions_table_and_duration():
    """Verify 5 permissions reference table and tracking duration formatting."""
    assert len(PERMISSIONS_HELP_DATA) == 5
    perm_names = [p[0] for p in PERMISSIONS_HELP_DATA]
    assert perm_names == ["Track", "Read", "Edit", "Index", "History"]

    table = render_permissions_help_table()
    assert table is not None

    start_dt = datetime.now() - timedelta(days=2, hours=3, minutes=15, seconds=30)
    dur_str = format_duration(start_dt)
    assert "Since" in dur_str
    assert "2d" in dur_str
    assert "3h" in dur_str

    tf = TrackedFolder(
        path="/tmp/test",
        track_allowed=True,
        read_allowed=True,
        edit_allowed=False,
        index_allowed=True,
        history_allowed=True,
    )
    badges = format_access_badges(tf)
    assert "[green]Track[/green]" in badges
    assert "[green]Read[/green]" in badges
    assert "[red]Edit[/red]" in badges
    assert "[green]Index[/green]" in badges
    assert "[green]History[/green]" in badges
    assert "[dim]|[/dim]" in badges

    archive_table = build_archive_table([tf])
    assert archive_table is not None

    assert get_path_item_type("/tmp") == "Folder"
    assert get_path_item_type(str(Path(__file__).resolve())) == "File"
    assert get_path_item_type("/home/user/document.pdf") == "File"


def test_argus_system_timestamps_and_incremental_ranges(tmp_path):
    """Verify Argus real system timestamps and incremental revision ranges."""
    from cli.commands.argus import (
        format_system_timestamps, get_window_tracking_info, format_recorded_editor_time,
    )
    from storage.models import WindowSession

    f = tmp_path / "test.txt"
    f.write_text("hello")
    ts_str = format_system_timestamps(str(f))
    assert "Created:" in ts_str
    assert "Modified:" in ts_str

    missing_str = format_system_timestamps(str(tmp_path / "does_not_exist.txt"))
    assert "Missing on disk" in missing_str

    t1 = datetime(2026, 10, 11, 4, 9, 48)
    t2 = datetime(2026, 10, 11, 4, 9, 55)
    t3 = datetime(2026, 10, 11, 4, 10, 2)
    ws = WindowSession(
        app_name="antigravity",
        window_title="antigravity test.py",
        start_time=t1,
        end_time=t3,
        duration_seconds=14.0,
    )
    r1, d1 = get_window_tracking_info("test.py", t1, [ws], prev_event_time=None)
    assert "04:09:48" in r1
    assert "antigravity" in d1

    r2, d2 = get_window_tracking_info("test.py", t2, [ws], prev_event_time=t1)
    assert "04:09:48 - 04:09:55" in r2
    assert "7s (antigravity)" in d2
    assert format_recorded_editor_time(r2, d2) == "2026-10-11 04:09:48 - 04:09:55\n7s (antigravity)"

    r3, d3 = get_window_tracking_info("test.py", t3, [ws], prev_event_time=t2)
    assert "04:09:55 - 04:10:02" in r3
    assert "7s (antigravity)" in d3

    from cli.commands.argus import clean_app_name, get_real_file_times
    assert clean_app_name('"org.gnome.TextEditor') == "Text Editor"
    assert clean_app_name("org.wezfurlong.wezterm") == "WezTerm"
    assert clean_app_name("code") == "VS Code"

    b_time, m_time = get_real_file_times(str(f))
    assert b_time is not None or m_time is not None

    # Verify priority: exact title match in editor takes precedence over generic session
    ws_ide = WindowSession(
        app_name="antigravity-ide",
        window_title="Antigravity IDE - main.py",
        start_time=t1,
        end_time=t3,
        duration_seconds=14.0,
    )
    ws_editor = WindowSession(
        app_name='"org.gnome.TextEditor',
        window_title='1.py (~/Downloads) - Text Editor',
        start_time=datetime(2026, 10, 11, 4, 9, 50),
        end_time=datetime(2026, 10, 11, 4, 9, 58),
        duration_seconds=8.0,
    )
    r_ed, d_ed = get_window_tracking_info(
        "1.py",
        datetime(2026, 10, 11, 4, 10, 0),
        [ws_ide, ws_editor],
        prev_event_time=datetime(2026, 10, 11, 4, 9, 45),
    )
    assert "Text Editor" in d_ed
    assert "antigravity" not in d_ed
    assert "8s (Text Editor)" in d_ed


def test_model_mask_key_and_discovery():
    """Verify API key masking and zero hardcoded model discovery when offline."""
    assert model_mask_key("sk-ant-api03-abcdefgh123456789") == "••••••••••••"
    assert model_mask_key("") == "[dim]none[/dim]"
    assert model_mask_key("123") == "••••••••••••"

    assert discover_models_for_provider("OpenAI", "") == []
    assert discover_models_for_provider("Claude", "") == []
    assert discover_models_for_provider("Ollama", base_url="http://localhost:9999") == []

    mock_openai_data = b'{"data": [{"id": "dynamic-model-a"}, {"id": "dynamic-model-b"}]}'
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = io.BytesIO(mock_openai_data)
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        models = discover_models_for_provider("OpenAI", api_key="sk-test")
        assert models == ["dynamic-model-a", "dynamic-model-b"]


def test_connector_mask_key():
    assert conn_mask_key("ghp_1234567890abcdef") == "••••••••••••"


def test_connector_catalog_is_gmail_calendar_flights_and_trains_only():
    assert list(CATALOG) == ["gmail", "calendar", "flights", "trains"]
    assert [item["label"] for item in CATALOG.values()] == ["Gmail", "Calendar", "Flights", "Trains"]


def test_window_time_parsing():
    """Verify time range parser handles multiple formats."""
    dt1 = parse_time_input("2026-10-10")
    assert dt1 is not None and dt1.year == 2026 and dt1.month == 10 and dt1.day == 10

    dt2 = parse_time_input("2026-10-10 14:30")
    assert dt2 is not None and dt2.hour == 14 and dt2.minute == 30

    dt3 = parse_time_input("15:45")
    assert dt3 is not None and dt3.hour == 15 and dt3.minute == 45


def test_boss_rules_file(tmp_path):
    """Verify boss rules file is generated and editable in configured editor."""
    class DummySkills:
        skills_dir = tmp_path

    class DummyShell:
        skills = DummySkills()

    f = get_boss_rules_file(DummyShell())
    assert f.exists()
    assert f.name == "boss_skills.md"
    assert "Boss Skills" in f.read_text()


def test_skills_table_and_slugify(tmp_path):
    """Verify slugify and skills table rendering."""
    from cli.commands.skills import slugify, render_skills_table
    from skills.registry import Skill

    assert slugify("Code Reviewer") == "code_reviewer"
    assert slugify("Git-Helper 2.0") == "git_helper_20"

    sk = Skill(
        id="sk1",
        name="Unit Tester",
        description="Write pytest tests",
        instructions="Generate comprehensive unit tests.",
        created_at="2026-10-10T12:00:00",
    )
    table = render_skills_table([sk])
    assert table is not None


def test_settings_and_preferred_editor(tmp_path):
    """Verify settings persistence and preferred editor configuration."""
    from config import SentienceConfig

    cfg = SentienceConfig(config_dir=tmp_path / "config")
    assert cfg.preferred_editor in ("nano", "vim", "vi") or len(cfg.preferred_editor) > 0

    cfg.set_setting("preferred_editor", "vim")
    assert cfg.preferred_editor == "vim"

    cfg.set_setting("preferred_editor", "code -w")
    assert cfg.preferred_editor == "code -w"

    from cli.shell import get_command_meta
    meta = get_command_meta("code -w")
    assert "code -w" in meta["skills"]
    assert "code -w" in meta["boss"]
    assert "code -w" in meta["settings"]


def test_terminal_shell_settings(tmp_path):
    """Verify terminal shell configuration persists and defaults to bash."""
    from config import SentienceConfig

    cfg = SentienceConfig(config_dir=tmp_path / "config")
    default_shell = cfg.get_setting("terminal_shell_name", "bash")
    assert default_shell == "bash"

    cfg.set_setting("terminal_shell_name", "zsh")
    cfg.set_setting("terminal_shell_path", "/usr/bin/zsh")
    assert cfg.get_setting("terminal_shell_name") == "zsh"
    assert cfg.get_setting("terminal_shell_path") == "/usr/bin/zsh"


def test_archive_edit_permissions_and_tracking_lifecycle(tmp_path):
    """Verify archive permissions update and tracking collector synchronization."""
    from storage.database import Database
    from storage.models import TrackedFolder, PrivacyLabel
    from config import SentienceConfig
    from computer.filesystem import FilesystemCollector

    db = Database(tmp_path / "test.db")
    cfg = SentienceConfig(config_dir=tmp_path / "config")
    collector = FilesystemCollector(db, cfg)

    folder_dir = tmp_path / "watch_me"
    folder_dir.mkdir()
    tf = TrackedFolder(
        path=str(folder_dir),
        label=PrivacyLabel.PUBLIC,
        track_allowed=True,
        read_allowed=True,
        edit_allowed=False,
        index_allowed=True,
        history_allowed=True,
    )
    saved = db.add_tracked_folder(tf)
    assert saved.id is not None

    folders = db.get_tracked_folders()
    assert len(folders) == 1
    assert folders[0].path == str(folder_dir)
    assert folders[0].track_allowed is True
    assert folders[0].edit_allowed is False

    collector.start_watching(saved.id, saved.path)
    assert saved.id in collector.watching

    db.update_folder_permissions(
        saved.id, track=False, read=True, edit=True, index=False, history=True
    )
    updated = db.get_folder(saved.id)
    assert updated.track_allowed is False
    assert updated.edit_allowed is True
    assert updated.index_allowed is False

    collector.stop_watching(saved.id)
    assert saved.id not in collector.watching

    db.remove_tracked_folder(saved.id)
    assert len(db.get_tracked_folders()) == 0
    collector.stop_all()
    db.close()


def test_argus_feature_and_history_inspection(tmp_path):
    """Verify Argus tracked table, event history, and content diff inspection."""
    from cli.commands.argus import build_argus_tracked_table
    from storage.database import Database
    from storage.models import TrackedFolder, PrivacyLabel, FileEvent, FileEventType
    from config import SentienceConfig
    from computer.git_history import ShadowGitManager

    db = Database(tmp_path / "argus_test.db")
    cfg = SentienceConfig(config_dir=tmp_path / "config", repos_dir=tmp_path / "repos")
    git = ShadowGitManager(cfg)

    folder_dir = tmp_path / "watched_project"
    folder_dir.mkdir()
    sample_file = folder_dir / "app.py"
    sample_file.write_text("print('version 1')\n")

    tf = TrackedFolder(
        path=str(folder_dir),
        label=PrivacyLabel.PUBLIC,
        track_allowed=True,
    )
    saved_folder = db.add_folder(tf)

    table = build_argus_tracked_table([saved_folder])
    assert table is not None
    assert "Argus" in str(table.title)

    ev1 = FileEvent(
        folder_id=saved_folder.id,
        event_type=FileEventType.CREATED,
        file_path="app.py",
        lines_added=1,
        lines_removed=0,
        timestamp=datetime.now(),
    )
    db.add_file_event(ev1)

    snap1 = git.snapshot(str(folder_dir))
    assert snap1 is not None

    sample_file.write_text("print('version 2 - modified')\nprint('new line added')\n")
    ev2 = FileEvent(
        folder_id=saved_folder.id,
        event_type=FileEventType.MODIFIED,
        file_path="app.py",
        lines_added=2,
        lines_removed=1,
        timestamp=datetime.now(),
    )
    db.add_file_event(ev2)

    snap2 = git.snapshot(str(folder_dir))
    assert snap2 is not None

    diff_text = git.diff(str(folder_dir), file_path="app.py")
    assert "+" in diff_text
    assert "-" in diff_text
    assert "version 2" in diff_text

    events = db.get_file_events(folder_id=saved_folder.id, file_path="app.py")
    assert len(events) == 2
    assert events[0].event_type == FileEventType.MODIFIED
    assert events[1].event_type == FileEventType.CREATED

    db.close()


def test_argus_line_diffs_and_open_duration(tmp_path):
    """Verify Argus line diff parser, duration formatting, and single-file tracking."""
    from cli.commands.argus import (
        parse_line_diffs,
        format_diff_snippet,
        format_open_duration,
        estimate_open_duration,
    )
    from storage.models import WindowSession
    from config import SentienceConfig
    from computer.git_history import ShadowGitManager

    sample_diff = """--- a/test.py
+++ b/test.py
@@ -10,3 +10,4 @@
 unchanged
-old code line
+new code line modified
+another added line
"""
    changes = parse_line_diffs(sample_diff)
    assert len(changes) == 3
    assert changes[0] == {"sign": "-", "line_no": 11, "col": 1, "content": "old code line"}
    assert changes[1] == {"sign": "+", "line_no": 11, "col": 1, "content": "new code line modified"}
    assert changes[2] == {"sign": "+", "line_no": 12, "col": 1, "content": "another added line"}

    snippet = format_diff_snippet(changes)
    assert "L11:C1 [-]" in snippet
    assert "L11:C1 [+]" in snippet
    assert "L12:C1 [+]" in snippet

    assert format_open_duration(0) == "< 1m"
    assert format_open_duration(25) == "25s"
    assert format_open_duration(125) == "2m 5s"
    assert format_open_duration(3600) == "1h 0m"

    now = datetime.now()
    ws = WindowSession(
        app_name="nano",
        window_title="nano test.py",
        start_time=now - timedelta(minutes=5),
        end_time=now,
        duration_seconds=300.0,
    )
    est = estimate_open_duration("test.py", now, [ws])
    assert "5m" in est
    assert "nano" in est

    cfg = SentienceConfig(config_dir=tmp_path / "config", repos_dir=tmp_path / "repos")
    git = ShadowGitManager(cfg)
    single_f = tmp_path / "single.txt"
    single_f.write_text("initial content\n")
    git.init_repo(str(single_f))
    s1 = git.snapshot(str(single_f))
    assert s1 is not None

    single_f.write_text("initial content\nmodified line\n")
    s2 = git.snapshot(str(single_f))
    assert s2 is not None
    assert s2["lines_added"] == 1

    diff = git.diff(str(single_f))
    assert "+modified line" in diff


def test_full_system_reset_wipes_all_data(tmp_path):
    """Verify reset_all_data purges database, providers.json, models, connectors, and history."""
    from cli.shell import SentienceShell
    import os

    isolated_env = {
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "LOCALAPPDATA": str(tmp_path / "localappdata"),
        "APPDATA": str(tmp_path / "appdata"),
    }
    with patch.dict(os.environ, isolated_env):
        shell = SentienceShell()
        # Add a provider with an API key and model
        p = shell.providers.add_provider("TestProvider", "cloud", api_key="sk-test-secret-key")
        shell.providers.add_model(p.id, "TestModel", "model-1")

        # Add a tracked folder
        folder_dir = tmp_path / "test_folder"
        folder_dir.mkdir()
        shell.db.add_tracked_folder(TrackedFolder(path=str(folder_dir)))

        # Add a connector
        conn_dir = shell.config.config_dir / "connectors"
        conn_dir.mkdir(parents=True, exist_ok=True)
        (conn_dir / "connectors.json").write_text('{"github": {"api_key": "ghp_123"}}')

        # Add window session
        shell.db.add_window_session(WindowSession(app_name="Gnome", window_title="Desktop", start_time=datetime.now()))

        assert len(shell.providers.get_providers()) > 0
        assert len(shell.providers.get_all_models()) > 0
        assert len(shell.db.get_tracked_folders()) > 0
        assert (conn_dir / "connectors.json").exists()

        # Execute full system wipe
        shell.reset_all_data()

        # Verify EVERYTHING is wiped clean
        assert len(shell.providers.get_providers()) == 0
        assert len(shell.providers.get_all_models()) == 0
        assert len(shell.db.get_tracked_folders()) == 0
        assert len(shell.db.get_window_sessions()) == 0
        assert not (conn_dir / "connectors.json").exists() or (conn_dir / "connectors.json").read_text() == "{}"

        shell.db.close()


def test_render_welcome_card_clean_daemon_status():
    """Verify welcome card displays clean daemon status."""
    shell = MagicMock()
    shell.daemon_active = True

    card = render_welcome_card(shell)
    c = Console(file=io.StringIO(), color_system=None)
    c.print(card)
    output = c.file.getvalue()

    assert "● Daemon Active" in output
    assert "Tab Complete" in output

    shell.daemon_active = False
    card_off = render_welcome_card(shell)
    c2 = Console(file=io.StringIO(), color_system=None)
    c2.print(card_off)
    output_off = c2.file.getvalue()

    assert "○ Daemon Off" in output_off
    assert "Tab Complete" in output_off


def test_provider_lifecycle(tmp_path):
    """Add provider → add model → switch model → remove."""
    pm = ProviderManager(tmp_path)

    assert pm.get_providers() == []
    assert pm.get_current_model() is None

    p = pm.add_provider("OpenAI", "cloud", api_key="sk-test123")
    assert p.name == "OpenAI"
    assert len(pm.get_providers()) == 1

    p2 = pm.add_provider("Ollama", "local", base_url="http://localhost:11434")
    assert p2.kind == "local"

    m1 = pm.add_model(p.id, "GPT-4o", "gpt-4o")
    assert m1 is not None
    assert m1.is_default is True

    m2 = pm.add_model(p2.id, "Llama 3", "llama3:8b")
    assert m2 is not None
    assert m2.is_local is True

    current = pm.get_current_model()
    assert current.id == m1.id

    assert pm.set_current_model(m2.id) is True
    assert pm.get_current_model().id == m2.id

    pm2 = ProviderManager(tmp_path)
    assert pm2.get_current_model().id == m2.id
    assert len(pm2.get_all_models()) == 2

    pm2.remove_provider(p.id)
    assert len(pm2.get_providers()) == 1


def test_known_models_catalog():
    """Verify zero hardcoded model presets in codebase (all fetched dynamically)."""
    assert ProviderManager.get_known_models("openai") == []
    assert ProviderManager.get_known_models("anthropic") == []
    assert ProviderManager.get_known_models("ollama") == []
    assert ProviderManager.get_known_models("google") == []
    assert ProviderManager.get_known_models("unknown_provider") == []


def test_skills_lifecycle(tmp_path):
    """Add skill → list → delete."""
    sm = SkillsManager(tmp_path)

    assert sm.get_skills() == []
    assert sm.get_boss_skills() == []

    s = sm.add_skill("Summarizer", "Summarize documents", trigger="When asked to summarize")
    assert s.name == "Summarizer"
    assert len(sm.get_skills()) == 1

    s2 = sm.add_skill("Code Review", "Review code for bugs")
    assert len(sm.get_skills()) == 2

    assert sm.remove_skill(s.id) is True
    assert len(sm.get_skills()) == 1

    bs = sm.add_boss_skill("User prefers dark mode", context="observed in settings", confidence=0.9)
    assert bs.observation == "User prefers dark mode"
    assert len(sm.get_boss_skills()) == 1

    sm2 = SkillsManager(tmp_path)
    assert len(sm2.get_skills()) == 1
    assert len(sm2.get_boss_skills()) == 1
