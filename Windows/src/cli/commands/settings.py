"""
SENTIENCE Settings Command.

Configures user preferences including:
- Preferred editor (user enters command directly: vim, nano, vi, code -w, micro, etc.)
  Replaces all editor calls across the system.
- Memory retention days
- Window sample interval
- Privacy operating mode
Prompt stays in sub-modes until explicit 'back' instruction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List

from cli.prompts import prompt as pt_prompt
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from cli.shell import SentienceShell

console = Console()


def sentinel_command(shell: SentienceShell, args: List[str]) -> None:
    """Dedicated Sentinel TUI for understanding and changing protection mode."""
    from sentinel.policy import SentinelMode, load_policy, save_policy

    mode_details = {
        SentinelMode.LOCAL: (
            "Maximum protection",
            "All agent work runs on local models. Sentinel scans content, detects secrets/PII, "
            "and blocks cloud routing entirely.",
            "LOCAL ONLY",
        ),
        SentinelMode.HYBRID: (
            "Protected cloud assistance",
            "Sentinel scans files, prompts, and tool results locally. Sensitive content stays local; "
            "safe content may be sanitized and sent to an eligible cloud model.",
            "SCAN + ROUTE",
        ),
        SentinelMode.CLOUD: (
            "No Sentinel protection",
            "Requests go directly to the configured cloud model. Privacy scanning, tokenization, "
            "and Sentinel routing are bypassed.",
            "DIRECT CLOUD",
        ),
    }

    while True:
        policy = load_policy(shell.config)
        decision = policy.task_decision()
        title = Text("SENTINEL  ·  PRIVACY & ROUTING", style="bold cyan")
        current = Table.grid(padding=(0, 1))
        current.add_column(style="cyan", justify="right")
        current.add_column(style="white")
        current.add_row("Active mode", f"[bold green]{policy.mode.value.upper()}[/bold green]")
        current.add_row("Cloud allowed", "[green]yes[/green]" if decision.cloud_allowed else "[yellow]no[/yellow]")
        current.add_row("Privacy scanning", "[green]enabled[/green]" if decision.privacy_checks_enabled else "[red]bypassed[/red]")
        current.add_row("Routing", "[green]enabled[/green]" if decision.routing_enabled else "[red]bypassed[/red]")
        current.add_row("Policy", decision.reason)

        modes = Table(box=None, expand=True, padding=(0, 1))
        modes.add_column("", width=3, style="bold yellow")
        modes.add_column("Mode", width=10, style="bold white")
        modes.add_column("Protection", width=20, style="cyan")
        modes.add_column("What Sentinel does", style="white")
        for number, candidate in enumerate(SentinelMode, 1):
            label, description, badge = mode_details[candidate]
            marker = "●" if candidate is policy.mode else "○"
            modes.add_row(str(number), f"{marker} {candidate.value.title()}", label, description)

        console.print(Panel(current, title=title, border_style="cyan", padding=(1, 2)))
        console.print(Panel(modes, title="Available modes", border_style="blue", padding=(1, 1)))
        console.print("[dim]Choose 1-3 or type local / hybrid / cloud. Type 'back' to return.[/dim]")
        choice = pt_prompt("sentinel > ").strip().lower()
        if choice in ("back", "exit", "quit", "q", "b", ""):
            return

        selected = {
            "1": SentinelMode.LOCAL, "local": SentinelMode.LOCAL,
            "2": SentinelMode.HYBRID, "hybrid": SentinelMode.HYBRID,
            "3": SentinelMode.CLOUD, "cloud": SentinelMode.CLOUD,
        }.get(choice)
        if selected is None:
            console.print("[red]Choose 1 (local), 2 (hybrid), 3 (cloud), or back.[/red]")
            continue
        if selected is SentinelMode.CLOUD:
            console.print(Panel(
                "[bold red]Warning: Cloud mode disables Sentinel privacy scanning and routing.[/bold red]\n\n"
                "Files, Argus context, prompts, and tool results may be sent directly to the configured cloud model.\n"
                "Type [bold]ENABLE CLOUD[/bold] to confirm, or 'back' to cancel.",
                border_style="red",
            ))
            if pt_prompt("sentinel [cloud] > ").strip() != "ENABLE CLOUD":
                console.print("[dim]Cloud mode was not enabled.[/dim]")
                continue
        save_policy(shell.config, selected)
        console.print(f"[bold green]✓ Sentinel mode set to {selected.value}.[/bold green]")


def _sub_editor_loop(shell: SentienceShell) -> None:
    """Stay inside settings [editor] until user inputs 'back'."""
    while True:
        current_editor = shell.config.preferred_editor
        console.print(Panel(
            f"[bold white]Preferred Editor Configuration[/bold white]\n\n"
            f"  [cyan]Current Editor Command:[/cyan] [bold green]{current_editor}[/bold green]\n\n"
            f"[dim]Enter any editor command you use in your terminal (e.g. {current_editor}, vim, nano, code -w, micro).\n"
            f"Sentience will use '{current_editor}' whenever opening files (skills, boss rules, etc.).\n"
            f"Type 'back' to return to settings menu.[/dim]",
            border_style="green",
        ))

        new_cmd = pt_prompt(f"settings [{current_editor}] > ").strip()
        if new_cmd.lower() in ("back", "exit", "quit", "q", "b", ":q", ""):
            break

        shell.config.set_setting("preferred_editor", new_cmd)
        try:
            from cli.shell import get_command_meta
            shell.command_meta = get_command_meta(new_cmd)
            shell.completer.commands_meta = shell.command_meta
        except Exception:
            pass
        console.print(f"\n[bold green]✓ Preferred editor successfully updated to:[/bold green] [bold cyan]{new_cmd}[/bold cyan]\n")


def _sub_retention_loop(shell: SentienceShell) -> None:
    """Configure memory retention days."""
    while True:
        curr = shell.config.get_setting("memory_retention_days", str(shell.config.memory_retention_days))
        console.print(Panel(
            f"[bold white]Memory Retention Configuration[/bold white]\n\n"
            f"  [cyan]Current Retention Period:[/cyan] [bold green]{curr} days[/bold green]\n\n"
            "[dim]Enter retention duration in days (e.g. 45, 60, 90). Type 'back' to return.[/dim]",
            border_style="green",
        ))

        val = pt_prompt("settings [retention] > ").strip()
        if val.lower() in ("back", "exit", "quit", "q", "b", ":q", ""):
            break

        if val.isdigit() and int(val) > 0:
            shell.config.set_setting("memory_retention_days", val)
            console.print(f"\n[bold green]✓ Retention period updated to {val} days.[/bold green]\n")
        else:
            console.print("[red]Please enter a valid positive number of days.[/red]")


def _sub_daemon_control(shell: SentienceShell) -> None:
    """Start or stop the background daemon."""
    if getattr(shell, "daemon_active", True):
        confirm = pt_prompt("Turn daemon OFF? Background monitoring will stop (y/N): ").strip().lower()
        if confirm in ("y", "yes"):
            shell._stop_daemon()
            shell.config.set_setting("daemon_enabled", "false")
            console.print("\n[bold yellow]✓ Background daemon turned OFF.[/bold yellow]\n")
        else:
            console.print("[dim]Cancelled.[/dim]")
    else:
        confirm = pt_prompt("Turn daemon ON? (y/N): ").strip().lower()
        if confirm in ("y", "yes"):
            shell._start_daemon()
            shell.config.set_setting("daemon_enabled", "true")
            console.print("\n[bold green]✓ Background daemon turned ON.[/bold green]\n")
        else:
            console.print("[dim]Cancelled.[/dim]")


def _sub_reset_db(shell: SentienceShell) -> None:
    """Completely wipe and re-initialize the entire database and all storage."""
    console.print("\n[bold red]CAUTION: Reset Database & Clean All Data[/bold red]")
    console.print("[dim]This will permanently wipe all conversation history, models, API keys, connectors, tracked folders, and window sessions.[/dim]\n")
    confirm = pt_prompt("Are you sure you want to completely clean and reset Sentience? (y/N): ").strip().lower()
    if confirm in ("y", "yes"):
        try:
            shell.reset_all_data()
            console.print("\n[bold green]✓ Complete system wiped and re-initialized successfully![/bold green]")
            console.print("  [dim]• Database tables and schemas re-initialized[/dim]")
            console.print("  [dim]• All API keys, providers, and models cleared[/dim]")
            console.print("  [dim]• All chats, tracked folder data, and window sessions deleted[/dim]")
            console.print("  [dim]• All connectors and command history wiped[/dim]\n")
        except Exception as e:
            console.print(f"\n[bold red]✗ Database reset failed: {e}[/bold red]\n")
    else:
        console.print("[dim]Database reset cancelled.[/dim]")


def _sub_window_tracker_guide(shell: SentienceShell) -> None:
    """Display platform-specific window tracking guidance."""
    import os

    if os.name == "nt":
        console.print()
        console.print(Panel(
            "[bold cyan]Windows Foreground Window Tracking[/bold cyan]\n\n"
            "  [white]Sentience reads the active desktop window through the Windows User32 API.[/white]\n"
            "  [white]No GNOME, Wayland, Looking Glass, or administrator permission is required.[/white]\n\n"
            "  [dim]Tracking samples the foreground window periodically and stores application name, title, and duration locally.[/dim]",
            border_style="cyan",
            padding=(1, 2),
        ))
        pt_prompt("Press Enter or type 'back' to return to settings > ").strip()
        return

    console.print()
    console.print(Panel(
        "[bold cyan]GNOME Wayland Window Tracking Setup Guide[/bold cyan]\n\n"
        "  [white]To manage window tracking on GNOME Wayland, use GNOME Looking Glass:[/white]\n\n"
        "  [bold yellow]Step 1:[/bold yellow] Press [bold white]Alt + F2[/bold white], type [bold green]lg[/bold green], and press [bold white]Enter[/bold white]\n"
        "  [bold yellow]Step 2:[/bold yellow] In the Looking Glass Evaluator prompt, enter the command:\n\n"
        "    • [bold green]To Enable (ON):[/bold green]   [bold white]global.context.unsafe_mode = true[/bold white]\n"
        "    • [bold yellow]To Disable (OFF):[/bold yellow] [bold white]global.context.unsafe_mode = false[/bold white]\n\n"
        "  [dim]Both commands are run directly in GNOME Looking Glass. Sentience does not take toggle input.[/dim]",
        border_style="cyan",
        padding=(1, 2),
    ))
    pt_prompt("Press Enter or type 'back' to return to settings > ").strip()


def _sub_terminal_shell_config(shell: SentienceShell) -> None:
    """Configure which shell executable is used in terminal mode."""
    import os
    import shutil

    if os.name == "nt":
        SHELL_CHOICES = {
            "1": ("powershell", "Windows PowerShell"),
            "2": ("pwsh", "PowerShell 7+"),
            "3": ("cmd", "Windows Command Prompt"),
        }
        supported_names = {name for name, _ in SHELL_CHOICES.values()} | {"powershell.exe", "pwsh.exe", "cmd.exe"}
        default_name = "powershell"
        path_hint = "Get-Command <shell_name> or enter the full .exe path"
    else:
        SHELL_CHOICES = {
            "1": ("bash", "Bourne Again Shell (Default)"),
            "2": ("zsh",  "Z Shell"),
            "3": ("fish", "Friendly Interactive Shell"),
            "4": ("sh",   "POSIX Standard Shell"),
            "5": ("dash", "Debian Almquist Shell"),
        }
        supported_names = {name for name, _ in SHELL_CHOICES.values()}
        default_name = "bash"
        path_hint = "which <shell_name>"

    while True:
        current_name = shell.config.get_setting("terminal_shell_name", default_name)
        default_path = shutil.which(current_name) or ("powershell.exe" if os.name == "nt" else "/bin/bash")
        current_path = shell.config.get_setting("terminal_shell_path", default_path)

        guide_lines = [
            "[bold cyan]Terminal Shell Configuration Guide[/bold cyan]\n",
            f"  [cyan]Current Configured Shell:[/cyan] [bold green]{current_name}[/bold green] ([dim]{current_path}[/dim])\n",
            f"[bold yellow]How to find your shell path:[/bold yellow]",
            f"  • Run [bold white]'{path_hint}'[/bold white] in your terminal",
            "  • Or enter the full path to the shell executable\n",
            "[bold yellow]Standard Supported Shells:[/bold yellow]",
        ]
        for k, (name, desc) in SHELL_CHOICES.items():
            guide_lines.append(f"  [bold yellow]{k}[/bold yellow]. [bold white]{name:<6}[/bold white] — [dim]{desc}[/dim]")
        guide_lines.append("\n[dim]Choose 1-5 or enter a custom shell name, or 'back' to return.[/dim]")

        console.print()
        console.print(Panel("\n".join(guide_lines), border_style="cyan", padding=(1, 2)))

        sel = pt_prompt("settings [terminal shell] > ").strip()
        if sel.lower() in ("back", "exit", "quit", "q", "b", ":q", ""):
            break

        selected_name = ""
        if sel in SHELL_CHOICES:
            selected_name = SHELL_CHOICES[sel][0]
        elif sel.lower() in supported_names:
            selected_name = sel.lower()
        elif sel:
            selected_name = sel.strip()

        detected = shutil.which(selected_name) or (f"{selected_name}.exe" if os.name == "nt" else f"/bin/{selected_name}")
        console.print(f"\n[cyan]Configuring shell:[/cyan] [bold green]{selected_name}[/bold green]")
        console.print(f"[dim]Default/detected path is:[/dim] [white]{detected}[/white]")

        custom_path = pt_prompt(f"Enter path to '{selected_name}' [{detected}] > ").strip()
        final_path = os.path.expandvars(os.path.expanduser(custom_path or detected))
        final_path = os.path.abspath(final_path)

        if not os.path.isfile(final_path) or not os.access(final_path, os.X_OK):
            console.print(f"[red]'{final_path}' is not an executable shell file.[/red]")
            console.print("[dim]Choose an installed shell or enter the path to an executable.[/dim]\n")
            continue

        shell.config.set_setting("terminal_shell_name", selected_name)
        shell.config.set_setting("terminal_shell_path", final_path)
        console.print(f"\n[bold green]✓ Terminal shell successfully configured to:[/bold green] [bold cyan]{selected_name}[/bold cyan] ([dim]{final_path}[/dim])\n")
        break


def settings_command(shell: SentienceShell, args: List[str]) -> None:
    """Interactive settings command with persistent sub-mode loops."""
    import shutil
    init_cmd = args[0].lower() if args else None

    while True:
        curr_editor = shell.config.preferred_editor
        curr_retention = shell.config.get_setting("memory_retention_days", str(shell.config.memory_retention_days))
        daemon_status = "[green]Active[/green]" if getattr(shell, "daemon_active", True) else "[yellow]Off[/yellow]"
        import os
        default_shell = "powershell" if os.name == "nt" else "bash"
        curr_shell_name = shell.config.get_setting("terminal_shell_name", default_shell)
        curr_shell_path = shell.config.get_setting(
            "terminal_shell_path",
            shutil.which(curr_shell_name) or ("powershell.exe" if os.name == "nt" else "/bin/bash"),
        )

        if not init_cmd:
            console.print()
            console.print(Panel(
                "[bold white]System Settings & Preferences[/bold white]\n"
                "[dim]Configure runtime preferences, editor commands, daemon status, and database[/dim]\n\n"
                f"  [bold yellow]1[/bold yellow]. [bold white]Preferred editor[/bold white]        [dim](current:[/dim] [cyan]{curr_editor}[/cyan][dim])[/dim]\n"
                f"  [bold yellow]2[/bold yellow]. [bold white]Memory retention[/bold white]        [dim](current:[/dim] [cyan]{curr_retention} days[/cyan][dim])[/dim]\n"
                f"  [bold yellow]3[/bold yellow]. [bold white]Daemon Control[/bold white]          [dim](status:[/dim] {daemon_status}[dim] — toggle daemon on/off)[/dim]\n"
                f"  [bold yellow]4[/bold yellow]. [bold white]Terminal Shell Config[/bold white]   [dim](current:[/dim] [cyan]{curr_shell_name}[/cyan] [dim]at[/dim] [dim]{curr_shell_path}[/dim][dim])[/dim]\n"
                f"  [bold yellow]5[/bold yellow]. [bold cyan]Window Tracker Guide[/bold cyan]    [dim](view platform tracking information)[/dim]\n"
                f"  [bold yellow]6[/bold yellow]. [bold red]Reset Database[/bold red]          [dim](wipe everything: db, models, api keys, connectors)[/dim]\n"
                "[dim]Sentinel privacy and routing are available as the top-level 'sentinel' command.[/dim]\n\n"
                "[dim]Type 1-6 or 'back' to return to sentience[/dim]",
                border_style="green",
            ))

            choice = pt_prompt("settings > ").strip().lower()
        else:
            choice = init_cmd
            init_cmd = None

        if choice in ("back", "exit", "quit", "q", "b", ":q", ""):
            break

        if choice in ("1", "editor", "text editor", curr_editor.lower()):
            _sub_editor_loop(shell)
        elif choice in ("2", "retention", "days"):
            _sub_retention_loop(shell)
        elif choice in ("3", "daemon", "daemon off", "daemon on"):
            _sub_daemon_control(shell)
        elif choice in ("4", "terminal", "shell", "terminal shell"):
            _sub_terminal_shell_config(shell)
        elif choice in ("5", "window", "tracker", "wayland", "lg"):
            _sub_window_tracker_guide(shell)
        elif choice in ("6", "reset", "reset db", "wipe"):
            _sub_reset_db(shell)
        else:
            console.print(f"[dim]Unknown option '{choice}'. Choose 1-6 or 'back'.[/dim]")
