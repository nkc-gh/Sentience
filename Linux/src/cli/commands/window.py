"""
SENTIENCE Window Tracking Command.

Shows window/application tracking data with:
1. View all: Last 45 days of captured window sessions in a clean, scrollable table.
2. Filter by time range: Clear examples for Year, Month, Date, Time formatting with interactive scrolling table.
3. Delete tracking history: Remove all sessions or whole sessions overlapping a date/time range.
Prompt stays on submodes until explicit 'back' instruction.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, List, Optional

from cli.prompts import prompt as pt_prompt
from rich import box
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

if TYPE_CHECKING:
    from cli.shell import SentienceShell

console = Console()


def parse_time_input(val: str, is_end: bool = False) -> Optional[datetime]:
    """Parse flexible user-entered 24-hour time or date strings."""
    if not val:
        return None
    val = val.strip()
    formats = [
        ("%Y-%m-%d %H:%M:%S", False),
        ("%Y-%m-%d %H:%M", False),
        ("%Y-%m-%d", True),
        ("%Y/%m/%d %H:%M:%S", False),
        ("%Y/%m/%d %H:%M", False),
        ("%Y/%m/%d", True),
        ("%Y-%m", True),
        ("%H:%M:%S", False),
        ("%H:%M", False),
    ]
    for fmt, is_date_only in formats:
        try:
            dt = datetime.strptime(val, fmt)
            if "%Y" not in fmt:
                today = datetime.now()
                dt = dt.replace(year=today.year, month=today.month, day=today.day)
                if is_end and fmt == "%H:%M":
                    dt = dt.replace(second=59)
            elif is_date_only and is_end:
                if fmt == "%Y-%m":
                    import calendar
                    _, last_day = calendar.monthrange(dt.year, dt.month)
                    dt = dt.replace(day=last_day, hour=23, minute=59, second=59)
                else:
                    dt = dt.replace(hour=23, minute=59, second=59)
            elif is_end and "%M" in fmt and "%S" not in fmt:
                dt = dt.replace(second=59)
            if is_end:
                dt = dt.replace(microsecond=999999)
            return dt
        except ValueError:
            pass
    return None


def format_session_time(s) -> str:
    """Format session time range strictly using 24-hour clock."""
    start = s.start_time
    end = s.end_time
    if not end or (s.duration_seconds is not None and s.duration_seconds <= 0):
        return f"{start.strftime('%Y-%m-%d %H:%M:%S')} (active)"
    if start.date() == end.date():
        return f"{start.strftime('%Y-%m-%d %H:%M:%S')} - {end.strftime('%H:%M:%S')}"
    return f"{start.strftime('%Y-%m-%d %H:%M:%S')} - {end.strftime('%Y-%m-%d %H:%M:%S')}"


def render_window_table(sessions: list, offset: int, page_size: int = 15) -> Panel:
    total = len(sessions)
    visible = sessions[offset : offset + page_size]
    start_idx = offset + 1
    end_idx = min(offset + page_size, total)

    table = Table(box=box.ROUNDED, border_style="green", padding=(0, 2), expand=True)
    table.add_column("#", justify="right", style="bold yellow", width=5)
    table.add_column("Time Range (24h)", style="bold cyan", width=38, no_wrap=False)
    table.add_column("Application", style="bold white", width=20, no_wrap=True)
    table.add_column("Window Title", style="dim white", ratio=1)
    table.add_column("Duration", justify="right", style="bold green", width=12, no_wrap=True)

    for idx, s in enumerate(visible, start_idx):
        tr = format_session_time(s)
        dur = f"{s.duration_seconds:.1f}s" if s.duration_seconds and s.duration_seconds > 0 else "active"
        app = s.app_name.strip('"')
        title = s.window_title.strip('"')
        if len(title) > 75:
            title = title[:72] + "..."
        table.add_row(str(idx), tr, app, title, dur)

    sub = f"[dim]{total} sessions recorded[/dim]"
    if total > page_size:
        sub = "  [cyan]↑/k[/cyan] up  ·  [cyan]↓/j[/cyan] down  ·  [cyan]q[/cyan] exit viewer  "

    return Panel(
        table,
        title=f"[bold green]Window Tracking History (Showing {start_idx}-{end_idx} of {total})[/bold green]",
        subtitle=sub,
        border_style="green",
        padding=(0, 1),
    )


def interactive_scroll_view(sessions: list) -> None:
    """Scrollable interactive view for window history."""
    if not sessions:
        console.print("\n[dim yellow]No window tracking sessions found.[/dim yellow]\n")
        return

    page_size = 15
    total = len(sessions)

    if total <= page_size or not sys.stdin.isatty():
        console.print()
        console.print(render_window_table(sessions, 0, page_size))
        console.print()
        return

    def read_nav_key() -> str:
        try:
            import termios, tty
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                ch = sys.stdin.read(1)
                if ch == "\x1b":
                    seq = sys.stdin.read(2)
                    if seq == "[A":
                        return "up"
                    if seq == "[B":
                        return "down"
                    return "esc"
                return ch
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            return "q"

    max_offset = max(0, total - page_size)
    offset = 0

    console.print()
    with Live(render_window_table(sessions, offset, page_size), console=console, auto_refresh=False) as live:
        while True:
            key = read_nav_key()
            if key in ("q", "Q", "esc", "\r", "\n", "\x03"):
                break
            elif key in ("up", "k", "K", "p"):
                offset = max(0, offset - 1)
            elif key in ("down", "j", "J", "n"):
                offset = min(max_offset, offset + 1)
            live.update(render_window_table(sessions, offset, page_size), refresh=True)
    console.print()


def _sub_view_all_loop(shell: SentienceShell) -> None:
    """Stay inside window [view all] until user inputs 'back'."""
    while True:
        since_dt = datetime.now() - timedelta(days=45)
        sessions = shell.db.get_window_sessions(since=since_dt, limit=5000)
        interactive_scroll_view(sessions)

        cmd = pt_prompt("window [view all] (Press Enter to view again, or 'back') > ").strip().lower()
        if cmd in ("back", "exit", "quit", "q", "b", ":q"):
            break


def _sub_filter_loop(shell: SentienceShell) -> None:
    """Stay inside window [filter] until user inputs 'back'."""
    while True:
        console.print(Panel(
            "[bold cyan]Time Range Filtering Guide (24-Hour Clock)[/bold cyan]\n\n"
            "  [yellow]Year, Month & Date:[/yellow] [bold white]2026-10-10[/bold white] (filters whole day)\n"
            "  [yellow]Date and Time:[/yellow]       [bold white]2026-10-10 14:30[/bold white] or [bold white]2026-10-10 14:30:00[/bold white]\n"
            "  [yellow]Time Only (Today):[/yellow]   [bold white]14:30[/bold white] or [bold white]09:15[/bold white]\n"
            "  [yellow]Year and Month:[/yellow]      [bold white]2026-10[/bold white]\n"
            "  [yellow]Inline Range:[/yellow]        [bold white]14:00 - 16:30[/bold white] or [bold white]2026-10-10 14:00 to 18:00[/bold white]\n\n"
            "[dim]Press Enter to leave Start or End unconstrained, or type 'back' to return.[/dim]",
            border_style="cyan",
        ))

        start_str = pt_prompt("window [filter] Start time (or range e.g. 14:00 - 16:00) > ").strip()
        if start_str.lower() in ("back", "exit", "quit", "q", "b"):
            break

        since_dt = None
        until_dt = None

        if " - " in start_str or " to " in start_str:
            sep = " - " if " - " in start_str else " to "
            p1, p2 = start_str.split(sep, 1)
            since_dt = parse_time_input(p1, is_end=False)
            until_dt = parse_time_input(p2, is_end=True)
            if not since_dt:
                console.print(f"[red]Could not parse start time in range:[/red] {p1}")
                continue
            if not until_dt:
                console.print(f"[red]Could not parse end time in range:[/red] {p2}")
                continue
        else:
            since_dt = parse_time_input(start_str, is_end=False) if start_str else None
            if start_str and not since_dt:
                console.print(f"[red]Could not parse start time:[/red] {start_str}")
                continue

            end_str = pt_prompt("window [filter] End time (or press Enter for none)   > ").strip()
            if end_str.lower() in ("back", "exit", "quit", "q", "b"):
                break
            until_dt = parse_time_input(end_str, is_end=True) if end_str else None
            if end_str and not until_dt:
                console.print(f"[red]Could not parse end time:[/red] {end_str}")
                continue

        sessions = shell.db.get_window_sessions(since=since_dt, until=until_dt, limit=10000)
        interactive_scroll_view(sessions)

        cmd = pt_prompt("window [filter] (Press Enter to filter again, or 'back') > ").strip().lower()
        if cmd in ("back", "exit", "quit", "q", "b", ":q"):
            break


def _sub_delete_loop(shell: SentienceShell) -> None:
    """Delete all window history or whole sessions overlapping a range."""
    while True:
        console.print(Panel(
            "[bold white]Delete Window Tracking History[/bold white]\n\n"
            "  [bold yellow]1[/bold yellow]. Delete all stored window sessions\n"
            "  [bold yellow]2[/bold yellow]. Delete sessions by date/time range\n\n"
            "[dim]Range deletion removes each whole session that overlaps the selected interval.[/dim]\n"
            "[dim]Type 'back' to return.[/dim]",
            border_style="red",
        ))
        choice = pt_prompt("window [delete] > ").strip().lower()
        if choice in ("back", "exit", "quit", "q", "b", ""):
            return
        if choice in ("1", "all", "delete all"):
            if pt_prompt("Permanently delete all recorded window sessions? Type DELETE to confirm > ").strip() != "DELETE":
                console.print("[dim]Deletion cancelled.[/dim]")
                continue
            tracker = getattr(shell, "window_tracker", None)
            if tracker:
                tracker.finalize_current_session()
            deleted = shell.db.delete_window_sessions()
            console.print(f"[green]Deleted {deleted} window session(s).[/green]")
            continue
        if choice in ("2", "range", "delete by range"):
            start_str = pt_prompt("Range start (YYYY-MM-DD, YYYY-MM-DD HH:MM[:SS], or HH:MM) > ").strip()
            if start_str.lower() in ("back", "exit", "quit", "q", "b"):
                continue
            since_dt = parse_time_input(start_str)
            if not since_dt:
                console.print("[red]Could not parse the range start.[/red]")
                continue
            end_str = pt_prompt("Range end (inclusive; same formats as start) > ").strip()
            if end_str.lower() in ("back", "exit", "quit", "q", "b"):
                continue
            until_dt = parse_time_input(end_str, is_end=True)
            if not until_dt:
                console.print("[red]Could not parse the range end.[/red]")
                continue
            if since_dt > until_dt:
                console.print("[red]The range start must be before or equal to its end.[/red]")
                continue
            matches = shell.db.get_window_sessions(since=since_dt, until=until_dt, limit=None)
            if not matches:
                console.print("[yellow]No window sessions overlap that range.[/yellow]")
                continue
            console.print(
                f"[yellow]{len(matches)} session(s) overlap {since_dt:%Y-%m-%d %H:%M:%S} "
                f"through {until_dt:%Y-%m-%d %H:%M:%S}. Each matching session will be deleted in full.[/yellow]"
            )
            if pt_prompt("Permanently delete these sessions? Type DELETE to confirm > ").strip() != "DELETE":
                console.print("[dim]Deletion cancelled.[/dim]")
                continue
            tracker = getattr(shell, "window_tracker", None)
            if tracker:
                tracker.finalize_current_session()
            deleted = shell.db.delete_window_sessions(since=since_dt, until=until_dt)
            console.print(f"[green]Deleted {deleted} window session(s).[/green]")
            continue
        console.print("[dim]Choose 1, 2 or 'back'.[/dim]")


def window_command(shell: SentienceShell, args: List[str]) -> None:
    """Browse application window tracking history with persistent sub-mode loops."""
    init_cmd = args[0].lower() if args else None

    while True:
        if not init_cmd:
            console.print()
            console.print(Panel(
                "[bold cyan]Desktop Window Activity & Wayland Guide[/bold cyan]\n\n"
                "  [bold yellow]GNOME Wayland Activation & Inspection:[/bold yellow]\n"
                "  1. Press [bold white]Alt + F2[/bold white], type [bold green]lg[/bold green], and press [bold white]Enter[/bold white] (opens GNOME Shell Looking Glass)\n"
                "  2. In the Looking Glass Evaluator prompt, you can toggle unsafe mode:\n"
                "     • To Enable (ON):   [bold green]global.context.unsafe_mode = true[/bold green]\n"
                "     • To Disable (OFF): [bold yellow]global.context.unsafe_mode = false[/bold yellow]\n"
                "  3. Switch to the [bold white]'Windows'[/bold white] tab in Looking Glass to view all native Wayland windows & titles\n"
                "  [dim]Window tracker automatically samples focused applications every 5 seconds.[/dim]",
                border_style="cyan",
            ))
            console.print(Panel(
                "[bold white]Window Activity Tracking[/bold white]\n"
                "[dim]Inspect window sessions captured across desktop applications[/dim]\n\n"
                "  [bold yellow]1[/bold yellow]. [bold white]View all (45 days)[/bold white] — Full 45-day history in clean scrollable table\n"
                "  [bold yellow]2[/bold yellow]. [bold white]Filter by time range[/bold white] — Precise filter by Year, Month, Date, and 24-hour Time\n\n"
                "  [bold yellow]3[/bold yellow]. [bold white]Delete tracking history[/bold white] — Delete all sessions or sessions in a range\n\n"
                "[dim]Type 1, 2, 3 or 'back' to return to sentience[/dim]",
                border_style="green",
            ))

            choice = pt_prompt("window > ").strip().lower()
        else:
            choice = init_cmd
            init_cmd = None

        if choice in ("back", "exit", "quit", "q", "b", ":q", ""):
            break

        if choice in ("1", "all", "view all"):
            _sub_view_all_loop(shell)
        elif choice in ("2", "filter", "range", "time"):
            _sub_filter_loop(shell)
        elif choice in ("3", "delete", "delete history"):
            _sub_delete_loop(shell)
        else:
            console.print(f"[dim]Unknown option '{choice}'. Choose 1, 2, 3 or 'back'.[/dim]")
