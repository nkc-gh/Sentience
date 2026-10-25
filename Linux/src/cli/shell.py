"""
SENTIENCE Interactive Shell.

The primary `sentience >` interface per spec §9.
Stoic dark aesthetic, reliable tab completion, and a focused command palette.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from cli.prompts import HomeRequested, prompt as pt_prompt, reset_prompt_screen, set_clear_header
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.shortcuts import CompleteStyle
from prompt_toolkit.styles import Style as PTStyle
from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from computer.directory_registry import TrackedFolderManager
from computer.filesystem import FilesystemCollector
from computer.git_history import ShadowGitManager
from computer.queries import MemoryQuery
from computer.shell_integration import ShellIntegration
from computer.window_tracker import WindowTracker
from config import SentienceConfig
from routing.registry import ProviderManager
from skills.registry import SkillsManager
from storage import initialize_storage
from storage.database import Database
from storage.models import GitSnapshot

console = Console()
logging.basicConfig(level=logging.WARNING)

# ═══════════════════════════════════════════════════════════
# Prompt Toolkit Style — Stoic dark aesthetic
# ═══════════════════════════════════════════════════════════

PT_STYLE = PTStyle.from_dict({
    "prompt.name": "#3fb950 bold",
    "prompt.sep":  "#236329 bold",
    "completion-menu":                     "bg:#161b22 #8b949e",
    "completion-menu.completion":          "bg:#161b22 #c9d1d9",
    "completion-menu.completion.current":  "bg:#238636 #ffffff bold",
    "completion-menu.meta":               "bg:#161b22 #6e7681",
    "completion-menu.meta.current":       "bg:#238636 #d2dbd6 italic",
    "scrollbar.background":               "bg:#0d1117",
    "scrollbar.button":                   "bg:#30363d",
})

SENTIENCE_ASCII = r"""[bold #58a6ff]
  ██████╗███████╗███╗   ██╗████████╗██╗███████╗███╗   ██╗ ██████╗███████╗
 ██╔════╝██╔════╝████╗  ██║╚══██╔══╝██║██╔════╝████╗  ██║██╔════╝██╔════╝
 ╚█████╗ █████╗  ██╔██╗ ██║   ██║   ██║█████╗  ██╔██╗ ██║██║     █████╗  
  ╚═══██╗██╔══╝  ██║╚██╗██║   ██║   ██║██╔══╝  ██║╚██╗██║██║     ██╔══╝  
 ██████╔╝███████╗██║ ╚████║   ██║   ██║███████╗██║ ╚████║╚██████╗███████╗
 ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝   ╚═╝╚══════╝╚═╝  ╚═══╝ ╚═════╝╚══════╝
[/bold #58a6ff]"""

MOTTO = "[#484f58]Remember Everything. Control What Matters.[/#484f58]"


def render_welcome_card(shell: SentienceShell) -> Panel:
    """Render sleek Sentience box with clean Daemon indicator and command guide."""
    daemon_ok = getattr(shell, "daemon_active", True)
    d_status = "[bold #3fb950]● Daemon Active[/bold #3fb950]" if daemon_ok else "[bold #d29922]○ Daemon Off[/bold #d29922]"
    box_border = "#3fb950" if daemon_ok else "#30363d"
    shortcuts_text = "[dim white]Tab Complete[/dim white]   [dim]·[/dim]   [dim white]↑↓ History[/dim white]   [dim]·[/dim]   [dim white]help Commands[/dim white]   [dim]·[/dim]   [dim white]exit Quit[/dim white]"

    return Panel(
        Group(
            Align.center(d_status),
            Text(""),
            Align.center(shortcuts_text),
        ),
        border_style=box_border,
        box=box.ROUNDED,
        padding=(1, 2),
        width=80,
    )


BANNER = SENTIENCE_ASCII
SUB_BANNER = MOTTO

# ═══════════════════════════════════════════════════════════
# Command Palette
# Direct point-to-point unclustered explanations
# ═══════════════════════════════════════════════════════════

def get_command_meta(preferred_editor: str = "") -> dict:
    """Generate command descriptions dynamically reflecting preferred editor."""
    ed = preferred_editor.strip() if preferred_editor else "editor"
    return {
        "help":       "Display all available commands with concise descriptions",
        "agent":      "Start or resume interactive conversational AI chat",
        "chats":      "Display active conversations or delete by ID",
        "archive":    "Manage tracked folders, permissions, and monitor duration",
        "argus":      "Inspect real-time filesystem activity, file changes, and revision history",
        "window":     "Inspect desktop window activity (45 days or time filter)",
        "model":      "Configure AI providers and dynamically discover models",
        "routing":    "Inspect and configure dynamic model routing policies",
        "sentinel":  "View and configure Sentinel privacy protection and routing modes",
        "connector":  "Manage third-party API integrations and service tokens",
        "skills":     f"Create, edit in {ed}, or delete custom AI skills",
        "boss":       f"Inspect learned user rules (Read mode or Read & Write in {ed})",
        "settings":   f"Configure preferences and preferred editor ({ed})",
        "terminal":   "Execute native shell commands interactively until back",
        "clear":      "Clear the visible terminal screen",
        "back":       "Return to the previous menu",
        "home":       "Return directly to the sentience > prompt",
        "exit":       "Gracefully shut down Sentience background daemon",
    }


COMMAND_META = get_command_meta("editor")


class SentienceCompleter(Completer):
    """
    Robust completer that reliably yields all commands on Tab press,
    both on empty buffer and after running multiple commands.
    """

    def __init__(self, commands_meta: dict):
        self.commands_meta = commands_meta

    def get_completions(self, document, complete_event):
        text_before = document.text_before_cursor
        stripped = text_before.strip().lower()
        commands = {**self.commands_meta, "back": "Return to the previous menu"}

        # If user is at prompt with no text, yield ALL available commands
        if not stripped:
            for cmd, desc in commands.items():
                yield Completion(
                    cmd,
                    start_position=0,
                    display=cmd,
                    display_meta=desc,
                )
        else:
            word_before = document.get_word_before_cursor(WORD=True).lower()
            prefix_matches = [
                (cmd, desc) for cmd, desc in commands.items()
                if cmd.lower().startswith(word_before)
            ]
            if prefix_matches:
                for cmd, desc in prefix_matches:
                    yield Completion(
                        cmd,
                        start_position=-len(word_before),
                        display=cmd,
                        display_meta=desc,
                    )
            else:
                for cmd, desc in commands.items():
                    if word_before in cmd.lower():
                        yield Completion(
                            cmd,
                            start_position=-len(word_before),
                            display=cmd,
                            display_meta=desc,
                        )


# ═══════════════════════════════════════════════════════════
# Sentience Shell Class
# ═══════════════════════════════════════════════════════════

class SentienceShell:
    """Interactive REPL — the primary `sentience >` interface."""

    def __init__(self):
        self.config = SentienceConfig()
        initialize_storage(self.config)

        # REPL History
        self.config.repl_history_path.parent.mkdir(parents=True, exist_ok=True)
        self.history = FileHistory(str(self.config.repl_history_path))

        # Core Subsystems
        self.db = Database(self.config.db_path)
        self.git = ShadowGitManager(self.config)
        self.tracker = TrackedFolderManager(self.db)
        self.queries = MemoryQuery(self.db, self.git)
        self.providers = ProviderManager(self.config.config_dir)
        self.skills = SkillsManager(self.config.skills_dir)
        self.skills.install_builtin_skills()
        self.session_id = self.providers.session_id

        # Background Collectors
        self.fs_collector = FilesystemCollector(self.db, self.config)
        # Window tracker: automatically active on daemon start
        self.window_tracker = WindowTracker(self.db, self.config.window_sample_interval_seconds)
        self.shell_integration = ShellIntegration(self.db, self.config)

        self.running = True
        self.daemon_active = self.config.get_setting("daemon_enabled", "true").lower() != "false"
        self._snapshot_thread = None

        # Command Dispatch Table
        self.commands = {
            "help":       self.cmd_help,
            "agent":      self.cmd_agent,
            "chats":      self.cmd_chats,
            "archive":    self.cmd_archive,
            "argus":      self.cmd_argus,
            "window":     self.cmd_window,
            "model":      self.cmd_model,
            "routing":    self.cmd_routing,
            "sentinel":  self.cmd_sentinel,
            "connector":  self.cmd_connector,
            "skills":     self.cmd_skills,
            "boss":       self.cmd_boss,
            "settings":   self.cmd_settings,
            "setting":    self.cmd_settings,
            "terminal":   self.cmd_terminal,
            "clear":      self.cmd_clear,
            "back":       self.cmd_back,
            "home":       self.cmd_home,
            "reset":      self.cmd_reset,
            "exit":       self.cmd_exit,
            "quit":       self.cmd_exit,
        }

        self.command_meta = get_command_meta(self.config.preferred_editor)
        self.completer = SentienceCompleter(self.command_meta)
        set_clear_header(self._print_header)

    # ── Daemon Lifecycle ─────────────────────────────────

    def _start_daemon(self):
        """Start background filesystem monitoring."""
        self.daemon_active = True
        for folder in self.tracker.get_active():
            if folder.track_allowed and folder.id:
                try:
                    self.fs_collector.start_watching(folder.id, folder.path)
                    self.git.init_repo(folder.path)
                    # Seed the shadow history before later edits arrive. If
                    # the first snapshot is delayed until after a user edit,
                    # Argus can mislabel that edit as file creation and lose
                    # its exact diff.
                    from cli.commands.argus import reconcile_file_tracking

                    reconcile_file_tracking(self, folder)
                except Exception as e:
                    logging.getLogger(__name__).warning("Failed to watch folder %s: %s", folder.path, e)

        if self.fs_collector.watching:
            self.db.set_tracking_state("on")
        else:
            self.db.set_tracking_state("off")

        try:
            self.db.cleanup_old_records(self.config.memory_retention_days)
        except Exception as e:
            logging.getLogger(__name__).warning("Cleanup failed: %s", e)

        self.shell_integration.install_hooks()
        self.shell_integration.start_reader()

        wt_enabled = self.config.get_setting("window_tracker_enabled", "true").lower() != "false"
        if wt_enabled and not self.window_tracker._running:
            self.window_tracker.start()

        if not self._snapshot_thread or not self._snapshot_thread.is_alive():
            self._snapshot_thread = threading.Thread(target=self._snapshot_loop, daemon=True)
            self._snapshot_thread.start()

    def _stop_daemon(self):
        """Gracefully stop background collectors without closing shell."""
        self.daemon_active = False
        self.fs_collector.stop_all()
        if hasattr(self, "window_tracker") and self.window_tracker:
            try:
                self.window_tracker.stop()
            except Exception:
                pass
        self.db.set_tracking_state("off")

    def _snapshot_loop(self):
        while self.running:
            time.sleep(self.config.snapshot_interval_seconds)
            if not self.running or not self.daemon_active:
                continue
            for folder in self.tracker.get_active():
                try:
                    result = self.git.snapshot(folder.path)
                    if result:
                        snap = GitSnapshot(
                            folder_id=folder.id,
                            commit_hash=result["commit_hash"],
                            timestamp=datetime.now(),
                            message=result["message"],
                            files_changed=result["files_changed"],
                            lines_added=result["lines_added"],
                            lines_removed=result["lines_removed"],
                        )
                        self.db.add_git_snapshot(snap)
                except Exception:
                    pass

    def reset_all_data(self) -> None:
        """Completely wipe all data: database tables, providers, models, API keys, connectors, history."""
        import json
        from dataclasses import asdict
        from routing.registry import TASK_MODES, TaskRoute

        # 1. Drop & re-init database schema
        self.db.reset_database()
        self.tracker = TrackedFolderManager(self.db)
        self.queries = MemoryQuery(self.db, self.git)
        self.fs_collector.db = self.db
        self.window_tracker.db = self.db

        # 2. Wipe all providers, models, and API keys
        self.providers._providers.clear()
        self.providers._current_model_id = None
        self.providers._routes = {mode: TaskRoute(task=mode) for mode in TASK_MODES}
        clean_provider_data = {
            "current_model_id": None,
            "providers": {},
            "routes": {mode: asdict(r) for mode, r in self.providers._routes.items()},
        }
        if self.providers._file.parent.exists():
            self.providers._file.write_text(json.dumps(clean_provider_data, indent=2), encoding="utf-8")

        # 3. Wipe external connectors
        from connectors.registry import ConnectorManager
        ConnectorManager(self.config.config_dir).clear_credentials()
        conn_file = self.config.config_dir / "connectors" / "connectors.json"
        if conn_file.exists():
            try:
                conn_file.unlink()
            except Exception:
                conn_file.write_text("{}", encoding="utf-8")

        # 4. Wipe history files
        if self.config.repl_history_path.exists():
            try:
                self.config.repl_history_path.write_text("", encoding="utf-8")
            except Exception:
                pass

        if self.config.shell_log_path.exists():
            try:
                self.config.shell_log_path.unlink()
            except Exception:
                pass

        # 5. Clear shadow git repos
        if self.config.repos_dir.exists():
            try:
                shutil.rmtree(self.config.repos_dir, ignore_errors=True)
                self.config.repos_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

    # ── Main Loop ────────────────────────────────────────

    def _print_header(self):
        """Display centered ASCII art, motto, and status card."""
        console.print()
        console.print(Align.center(SENTIENCE_ASCII))
        console.print(Align.center(MOTTO))
        console.print()
        console.print(Align.center(render_welcome_card(self)))
        console.print()

    def run(self):
        """Main REPL loop — `sentience >`."""
        # Start each interactive session on a clean screen, matching the
        # behavior of the in-session `clear` command.
        os.system("cls" if os.name == "nt" else "clear")
        if self.daemon_active:
            self._start_daemon()
        else:
            self._stop_daemon()
        self._print_header()

        while self.running:
            try:
                raw = pt_prompt(
                    HTML("<prompt.name>sentience</prompt.name> <prompt.sep>&gt;</prompt.sep> "),
                    style=PT_STYLE,
                    completer=self.completer,
                    history=self.history,
                    complete_while_typing=True,
                    complete_style=CompleteStyle.MULTI_COLUMN,
                    reserve_space_for_menu=8,
                    handle_clear=False,
                ).strip()
            except HomeRequested:
                self._print_header()
                continue
            except (EOFError, KeyboardInterrupt):
                self.cmd_exit()
                break

            if not raw:
                continue

            parts = raw.split(None, 1)
            cmd_key = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            if cmd_key in self.commands:
                try:
                    reset_prompt_screen()
                    self.commands[cmd_key](args)
                except HomeRequested:
                    self._print_header()
                except Exception as e:
                    console.print(f"  [red]error:[/red] {e}")
            else:
                console.print(f"  [#6e7681]unknown command: {cmd_key} — press Tab near sentience > for commands[/#6e7681]")

            console.print()

    # ═════════════════════════════════════════════════════
    # Command Handlers
    # ═════════════════════════════════════════════════════

    def cmd_help(self, args=""):
        """Show all available commands with direct, unclustered one-line explanations."""
        if args.strip():
            cmd = args.strip().lower()
            if cmd in COMMAND_META:
                console.print(f"\n  [bold green]{cmd}[/bold green]  {COMMAND_META[cmd]}")
            else:
                console.print(f"  [#6e7681]unknown command: {cmd}[/#6e7681]")
            return

        table = Table(
            box=box.SIMPLE,
            border_style="#30363d",
            show_header=True,
            header_style="bold cyan",
            padding=(0, 2),
        )
        table.add_column("Command", style="bold green", min_width=14)
        table.add_column("Explanation", style="white")

        meta = get_command_meta(self.config.preferred_editor)
        for cmd, desc in meta.items():
            table.add_row(cmd, desc)

        console.print()
        console.print(Align.center(Panel(
            table,
            title="[bold white]Sentience Command Palette[/bold white]",
            subtitle="[dim]Press Tab at sentience > for suggestions · Type command name to run[/dim]",
            border_style="green",
            box=box.ROUNDED,
            padding=(1, 2),
            expand=False,
        )))

    def cmd_agent(self, args=""):
        """Interactive AI agent chatting (continue chat or new chat)."""
        from cli.commands.agent import agent_mode
        agent_mode(self)

    def cmd_chats(self, args=""):
        """Manage conversations (show 12k/200k, delete with contiguous renumbering)."""
        from cli.commands.chats import chats_command
        arg_list = [args.strip()] if args.strip() else []
        chats_command(self, arg_list)

    def cmd_archive(self, args=""):
        """Manage tracked items, permissions table, tracking duration, and picker."""
        from cli.commands.archive import run_archive_command
        arg_list = [args.strip()] if args.strip() else []
        run_archive_command(self, arg_list)

    def cmd_argus(self, args=""):
        """Inspect real-time filesystem activity, file changes, and revision history."""
        from cli.commands.argus import argus_command
        arg_list = [args.strip()] if args.strip() else []
        argus_command(self, arg_list)

    def cmd_window(self, args=""):
        """Browse, filter, or delete application window history."""
        from cli.commands.window import window_command
        arg_list = [args.strip()] if args.strip() else []
        window_command(self, arg_list)

    def cmd_model(self, args=""):
        """Manage providers with dynamic model discovery, list, and removal."""
        from cli.commands.model import model_command
        arg_list = [args.strip()] if args.strip() else []
        model_command(self, arg_list)

    def cmd_routing(self, args=""):
        """Dedicated dynamic model and privacy routing."""
        from cli.commands.routing import routing_command
        arg_list = [args.strip()] if args.strip() else []
        routing_command(self, arg_list)

    def cmd_sentinel(self, args=""):
        """View and configure Sentinel privacy and routing protection."""
        from cli.commands.settings import sentinel_command
        arg_list = [args.strip()] if args.strip() else []
        sentinel_command(self, arg_list)

    def cmd_connector(self, args=""):
        """Connect, test, edit, or disconnect external services."""
        from cli.commands.connector import connector_command
        arg_list = [args.strip()] if args.strip() else []
        connector_command(self, arg_list)

    def cmd_skills(self, args=""):
        """Create, edit in configured editor, or delete custom AI skills."""
        from cli.commands.skills import skills_command
        arg_list = [args.strip()] if args.strip() else []
        skills_command(self, arg_list)

    def cmd_boss(self, args=""):
        """Inspect learned user rules, view or edit in configured editor, and delete."""
        from cli.commands.boss import boss_command
        arg_list = [args.strip()] if args.strip() else []
        boss_command(self, arg_list)

    def cmd_settings(self, args=""):
        """Configure user preferences and preferred editor."""
        from cli.commands.settings import settings_command
        arg_list = [args.strip()] if args.strip() else []
        settings_command(self, arg_list)
        # Refresh command metadata and completer with newly chosen editor
        self.command_meta = get_command_meta(self.config.preferred_editor)
        self.completer.commands_meta = self.command_meta

    def cmd_terminal(self, args=""):
        """Execute native terminal commands interactively until back."""
        from cli.commands.terminal import terminal_command
        arg_list = [args.strip()] if args.strip() else []
        terminal_command(self, arg_list)

    def cmd_clear(self, args=""):
        """Clear visible terminal output."""
        os.system("clear" if os.name != "nt" else "cls")
        self._print_header()

    def cmd_back(self, args=""):
        """At the top-level prompt, back is already at the root menu."""
        console.print("[dim]Already at the Sentience menu.[/dim]")

    def cmd_home(self, args=""):
        """Return to the Sentience root prompt."""
        self._print_header()

    def cmd_reset(self, args=""):
        """Completely reset all database and system data."""
        self.cmd_settings("6")

    def cmd_exit(self, args=""):
        """Gracefully exit Sentience and shut down daemon."""
        console.print("\n  [#484f58]stopping tracking collectors...[/#484f58]")
        self.db.set_tracking_state("off")
        self.running = False
        self._stop_daemon()
        console.print("  [#484f58]closing database...[/#484f58]")
        self.db.close()
        console.print("  [#c9d1d9]goodbye.[/#c9d1d9]\n")


def main():
    """Main entrypoint for the interactive shell."""
    shell = SentienceShell()
    shell.run()


if __name__ == "__main__":
    main()
