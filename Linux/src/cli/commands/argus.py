"""
SENTIENCE Argus Command.

Filesystem Activity & Change Inspector:
- Displays table of all tracked roots with full visible paths, types, access, and durations.
- S.No selection opens full 45-day history for the chosen root.
- For Single Files:
  * Shows when the file was created (creation date & time).
  * Lists revisions chronologically with exact dates and times.
  * Shows for how long the file was open / being edited (correlated from window sessions).
  * Shows exact line diffs with line numbers and columns (e.g. L14:C1 [+] / [-]).
  * If the file is deleted, clearly reports 'File deleted' at the end.
  * Supports pagination / navigation ('n'/'p' pages, 'cat' to view content, 'back' to return).
- For Folders:
  * Lists active, modified, and deleted files with change counts.
  * Choosing any file drills down into its full single-file history & diffs.
Prompt stays on selected options until explicit 'back'.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from cli.prompts import prompt as pt_prompt
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from cli.commands.archive import (
    format_access_badges,
    format_duration,
    get_path_item_type,
)
from storage.models import (
    FileEvent,
    FileEventType,
    GitSnapshot,
    TrackedFolder,
    WindowSession,
)

if TYPE_CHECKING:
    from cli.shell import SentienceShell

logger = logging.getLogger(__name__)
console = Console()


def get_real_file_times(path: Union[Path, str]) -> tuple[Optional[datetime], Optional[datetime]]:
    """Retrieve real filesystem creation (birth/btime) and last modified (mtime) timestamps."""
    p = Path(path)
    if not p.exists():
        return None, None

    birth_dt: Optional[datetime] = None
    mtime_dt: Optional[datetime] = None

    try:
        res = subprocess.run(
            ["stat", "-c", "%w|%y", str(p)],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if res.returncode == 0 and "|" in res.stdout:
            raw_w, raw_y = res.stdout.strip().split("|", 1)
            if raw_w != "-":
                try:
                    dt = datetime.fromisoformat(raw_w.strip())
                    birth_dt = dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
                except Exception:
                    pass
            if raw_y != "-":
                try:
                    dt = datetime.fromisoformat(raw_y.strip())
                    mtime_dt = dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
                except Exception:
                    pass
    except Exception:
        pass

    try:
        st = p.stat()
        if not mtime_dt:
            mtime_dt = datetime.fromtimestamp(st.st_mtime)
        if not birth_dt:
            b = getattr(st, "st_birthtime", None)
            birth_dt = datetime.fromtimestamp(b if b else st.st_ctime)
    except Exception:
        pass

    return birth_dt, mtime_dt


def get_batch_file_times(paths: List[Path]) -> Dict[str, tuple[Optional[datetime], Optional[datetime]]]:
    """Batch retrieve real filesystem birth and modify timestamps for multiple paths."""
    result: Dict[str, tuple[Optional[datetime], Optional[datetime]]] = {}
    if not paths:
        return result
    path_strs = [str(p) for p in paths]
    for i in range(0, len(path_strs), 400):
        chunk = path_strs[i : i + 400]
        try:
            res = subprocess.run(
                ["stat", "-c", "%n|%w|%y"] + chunk,
                capture_output=True,
                text=True,
                timeout=4,
            )
            for line in res.stdout.splitlines():
                if "|" in line:
                    parts = line.split("|")
                    if len(parts) >= 3:
                        fn, raw_w, raw_y = parts[0], parts[1], parts[2]
                        b_dt = None
                        m_dt = None
                        if raw_w != "-":
                            try:
                                dt = datetime.fromisoformat(raw_w.strip())
                                b_dt = dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
                            except Exception:
                                pass
                        if raw_y != "-":
                            try:
                                dt = datetime.fromisoformat(raw_y.strip())
                                m_dt = dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
                            except Exception:
                                pass
                        result[fn] = (b_dt, m_dt)
        except Exception:
            pass
    return result


def format_system_timestamps(path_str: str) -> str:
    """Retrieve real filesystem creation (birth/ctime) and last modified timestamps."""
    p = Path(path_str)
    if not p.exists():
        return "[red]Missing on disk[/red]"
    b_time, m_time = get_real_file_times(p)
    if not b_time and not m_time:
        return "[dim]Unavailable[/dim]"
    c_str = b_time.strftime("%Y-%m-%d %H:%M") if b_time else (m_time.strftime("%Y-%m-%d %H:%M") if m_time else "-")
    m_str = m_time.strftime("%Y-%m-%d %H:%M") if m_time else "-"
    return f"[dim]Created:[/dim]  [cyan]{c_str}[/cyan]\n[dim]Modified:[/dim] [green]{m_str}[/green]"


def build_argus_tracked_table(folders: List[TrackedFolder]) -> Table:
    """Build table of all tracked filesystem roots with full visible paths."""
    table = Table(
        title="[bold green]Argus — Tracked Filesystem Roots[/bold green]",
        show_header=True,
        header_style="bold cyan",
        padding=(0, 1),
    )
    table.add_column("S.No", justify="right", style="bold yellow", width=6)
    table.add_column("Name", style="bold white", width=18)
    table.add_column("Path", style="dim", min_width=32, ratio=2, overflow="fold")
    table.add_column("Type", width=12)
    table.add_column("Access", width=42)
    table.add_column("Created / Modified", width=28)

    for idx, folder in enumerate(folders, 1):
        p = Path(folder.path)
        name = p.name or str(p)
        item_type = get_path_item_type(folder.path)
        type_str = f"[blue]📁 Folder[/blue]" if item_type == "Folder" else f"[yellow]📄 File[/yellow]"
        time_str = format_system_timestamps(folder.path)
        access_str = format_access_badges(folder)

        table.add_row(
            str(idx),
            name,
            str(folder.path),
            type_str,
            access_str,
            time_str,
        )
    return table


def render_diff_view(diff_text: str, title: str) -> None:
    """Render unified diff with rich syntax highlighting (+ in green, - in red)."""
    if not diff_text.strip():
        console.print(Panel(
            "[dim yellow]No textual diff changes recorded for this revision.[/dim yellow]",
            title=f"[bold green]{title}[/bold green]",
            border_style="green",
        ))
        return

    syntax = Syntax(diff_text, "diff", theme="monokai", line_numbers=True, word_wrap=True)
    console.print(Panel(syntax, title=f"[bold green]{title}[/bold green]", border_style="green"))


def _format_event_badge(event_type: FileEventType) -> str:
    if event_type == FileEventType.CREATED:
        return "[bold green]CREATED[/bold green]"
    elif event_type == FileEventType.MODIFIED:
        return "[bold yellow]MODIFIED[/bold yellow]"
    elif event_type == FileEventType.DELETED:
        return "[bold red]DELETED[/bold red]"
    elif event_type == FileEventType.RENAMED:
        return "[bold cyan]RENAMED[/bold cyan]"
    elif event_type == FileEventType.MOVED:
        return "[bold magenta]MOVED[/bold magenta]"
    return f"[dim]{event_type.value}[/dim]"


def to_naive_datetime(dt: Any) -> Optional[datetime]:
    """Convert any datetime, numeric timestamp, or ISO string to a timezone-naive local datetime."""
    if dt is None:
        return None
    if isinstance(dt, (int, float)):
        return datetime.fromtimestamp(dt)
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except Exception:
            return None
    if isinstance(dt, datetime):
        if dt.tzinfo is not None:
            return dt.astimezone().replace(tzinfo=None)
        return dt
    return None


def parse_line_diffs(diff_text: str) -> List[Dict[str, Any]]:
    """Parse unified diff into structured line changes with line numbers, columns, and snippets."""
    changes: List[Dict[str, Any]] = []
    old_line = 0
    new_line = 0
    hunk_re = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")

    for raw_line in diff_text.splitlines():
        if (
            raw_line.startswith("diff --git")
            or raw_line.startswith("index ")
            or raw_line.startswith("---")
            or raw_line.startswith("+++")
        ):
            continue
        m = hunk_re.match(raw_line)
        if m:
            old_line = int(m.group(1))
            new_line = int(m.group(2))
            continue
        if raw_line.startswith("-"):
            content = raw_line[1:]
            stripped = content.lstrip()
            col = (len(content) - len(stripped) + 1) if stripped else 1
            changes.append({"sign": "-", "line_no": old_line, "col": col, "content": content})
            old_line += 1
        elif raw_line.startswith("+"):
            content = raw_line[1:]
            stripped = content.lstrip()
            col = (len(content) - len(stripped) + 1) if stripped else 1
            changes.append({"sign": "+", "line_no": new_line, "col": col, "content": content})
            new_line += 1
        elif raw_line.startswith(" "):
            old_line += 1
            new_line += 1
    return changes


def format_diff_snippet(changes: List[Dict[str, Any]], max_lines: int = 3) -> str:
    """Format compact multi-line diff snippet showing exact line number, column, and snippet."""
    if not changes:
        return "[dim]Diff unavailable for this revision[/dim]"

    lines: List[str] = []
    for ch in changes[:max_lines]:
        sign = ch["sign"]
        line_no = ch["line_no"]
        col = ch["col"]
        text = ch["content"].rstrip("\r\n")
        if len(text) > 42:
            text = text[:39] + "..."
        if sign == "+":
            lines.append(f"[green]L{line_no}:C{col} [+] {text}[/green]")
        else:
            lines.append(f"[red]L{line_no}:C{col} [-] {text}[/red]")

    if len(changes) > max_lines:
        lines.append(f"[dim]... (+{len(changes) - max_lines} more lines)[/dim]")

    return "\n".join(lines)


def clean_app_name(app_name: str) -> str:
    """Format window application identifiers into human-friendly names."""
    if not app_name:
        return ""
    name = app_name.strip("'\" ").strip()
    mapping = {
        "org.gnome.TextEditor": "Text Editor",
        "org.gnome.gedit": "Gedit",
        "org.wezfurlong.wezterm": "WezTerm",
        "org.gnome.Nautilus": "Files",
        "org.kde.kate": "Kate",
        "code": "VS Code",
        "com.visualstudio.code": "VS Code",
        "sublime_text": "Sublime Text",
        "google-chrome": "Chrome",
    }
    if name in mapping:
        return mapping[name]
    if "." in name:
        last = name.split(".")[-1]
        cleaned = re.sub(r"([a-z])([A-Z])", r"\1 \2", last)
        return cleaned.strip()
    return name


def format_open_duration(seconds: float) -> str:
    """Format elapsed seconds into readable human duration."""
    if seconds <= 0:
        return "< 1m"
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    m = s // 60
    rem_s = s % 60
    if m < 60:
        return f"{m}m {rem_s}s" if rem_s > 0 else f"{m}m"
    h = m // 60
    rem_m = m % 60
    return f"{h}h {rem_m}m"


def format_recorded_editor_time(time_range: str, duration: str) -> str:
    """Show the observed session boundaries as well as its measured duration."""
    if not duration or duration == "-":
        return duration or "-"
    if not time_range or time_range == "-":
        return duration
    return f"{time_range}\n{duration}"


def get_window_tracking_info(
    target_name: str,
    event_time: datetime,
    window_sessions: List[WindowSession],
    prev_event_time: Optional[datetime] = None,
    is_created: bool = False,
) -> tuple[str, str]:
    """Return only recorded window time whose title identifies this file."""
    event_time = to_naive_datetime(event_time) or datetime.now()
    prev_event_time = to_naive_datetime(prev_event_time)
    if is_created:
        return (event_time.strftime("%Y-%m-%d %H:%M:%S"), "-")
    from computer.window_correlation import match_file_editor_sessions

    match = match_file_editor_sessions(target_name, event_time, window_sessions, prev_event_time)
    if not match:
        return (event_time.strftime("%Y-%m-%d %H:%M:%S"), "not recorded (no matching file window)")
    start, end = match["start"], match["end"]
    if start.date() == end.date():
        time_range = f"{start:%Y-%m-%d %H:%M:%S} - {end:%H:%M:%S}"
    else:
        time_range = f"{start:%Y-%m-%d %H:%M:%S} - {end:%Y-%m-%d %H:%M:%S}"
    apps = ", ".join(clean_app_name(app) for app in match["apps"])
    duration = format_open_duration(float(match["seconds"]))
    return time_range, f"{duration} ({apps})" if apps else duration


def estimate_open_duration(
    target_name: str,
    event_time: datetime,
    window_sessions: List[WindowSession],
    prev_event_time: Optional[datetime] = None,
) -> str:
    """Correlate active window sessions or editing intervals for open duration."""
    _, dur = get_window_tracking_info(
        target_name, event_time, window_sessions, prev_event_time=prev_event_time
    )
    return dur


def _parse_commit_file_changes(
    git_manager: Any,
    folder_path: str,
    commit_hash: str,
) -> List[Dict[str, Any]]:
    """Parse files changed in a git commit with status and line additions/deletions."""
    repo = git_manager._repo_path(folder_path)
    if not (repo / ".git").exists():
        return []
    try:
        numstat_raw = git_manager._git(repo, ["show", "--numstat", "--format=", commit_hash])
        namestat_raw = git_manager._git(repo, ["show", "--name-status", "--format=", commit_hash])
    except Exception:
        return []

    status_by_file: Dict[str, str] = {}
    for line in namestat_raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            st = parts[0]
            fp = parts[-1]
            status_by_file[fp] = st

    numstat_by_file: Dict[str, tuple[int, int]] = {}
    for line in numstat_raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            add_s, del_s, fp = parts[0], parts[1], parts[2]
            added = int(add_s) if add_s.isdigit() else 0
            removed = int(del_s) if del_s.isdigit() else 0
            numstat_by_file[fp] = (added, removed)

    all_files = set(status_by_file.keys()) | set(numstat_by_file.keys())
    results: List[Dict[str, Any]] = []
    for fp in all_files:
        if any(part.startswith(".") for part in Path(fp).parts):
            continue
        st = status_by_file.get(fp, "M")
        added, removed = numstat_by_file.get(fp, (0, 0))
        if st.startswith("A"):
            event_type = FileEventType.CREATED
        elif st.startswith("D"):
            event_type = FileEventType.DELETED
        else:
            event_type = FileEventType.MODIFIED

        results.append({
            "file_path": fp,
            "event_type": event_type,
            "lines_added": added,
            "lines_removed": removed,
        })
    return results


def _sync_folder_commits_to_events(shell: SentienceShell, folder: TrackedFolder) -> None:
    """Ensure all shadow git commits are properly represented in the file_events database."""
    folder_p = Path(folder.path)
    if not folder_p.is_dir():
        return

    try:
        conn = shell.db._get_conn()
        conn.execute(
            "DELETE FROM file_events WHERE folder_id = ? AND (file_path = ? OR file_path = '' OR file_path = '.' OR file_path LIKE '.git%')",
            (folder.id, folder_p.name),
        )
        conn.commit()

        repo = shell.git._repo_path(folder.path)
        if not (repo / ".git").exists():
            return

        existing_commits = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT git_commit FROM file_events WHERE folder_id = ? AND git_commit IS NOT NULL",
                (folder.id,),
            ).fetchall()
        }

        commits = shell.git.log(folder.path, limit=None)
        for c in reversed(commits):
            c_hash = c["commit"]
            if c_hash in existing_commits:
                continue
            c_ts = to_naive_datetime(c["timestamp"]) or datetime.now()
            changes = _parse_commit_file_changes(shell.git, folder.path, c_hash)
            for ch in changes:
                ev = FileEvent(
                    folder_id=folder.id,
                    event_type=ch["event_type"],
                    file_path=ch["file_path"],
                    timestamp=c_ts,
                    git_commit=c_hash,
                    lines_added=ch["lines_added"],
                    lines_removed=ch["lines_removed"],
                    is_directory=False,
                    source="argus_sync",
                )
                shell.db.add_file_event(ev)
    except Exception as e:
        logger.debug("Error syncing folder commits to events: %s", e)


def reconcile_file_tracking(
    shell: SentienceShell,
    folder: TrackedFolder,
    rel_path: str = "",
) -> None:
    """Sync disk state with shadow git and ensure database file events are up to date."""
    def record_snapshot(snap: Dict) -> None:
        """Persist a snapshot and merge its changes with watcher events.

        Watchdog normally records an event before Argus creates the matching
        shadow-Git commit. Reusing that event preserves one logical history
        entry and enriches it with the exact commit and line counts.
        """
        snap_record = GitSnapshot(
            folder_id=folder.id,
            commit_hash=snap["commit_hash"],
            timestamp=datetime.now(),
            message=snap["message"],
            files_changed=snap["files_changed"],
            lines_added=snap["lines_added"],
            lines_removed=snap["lines_removed"],
        )
        shell.db.add_git_snapshot(snap_record)

        commit_time = datetime.now()
        changes = _parse_commit_file_changes(shell.git, folder.path, snap["commit_hash"])
        for change in changes:
            file_events = shell.db.get_file_events(
                folder_id=folder.id,
                file_path=change["file_path"],
                limit=None,
            )
            matching = next(
                (
                    event
                    for event in file_events
                    if event.git_commit is None
                    and event.event_type == change["event_type"]
                    and abs((commit_time - event.timestamp).total_seconds()) <= 30
                ),
                None,
            )
            if matching and matching.id is not None:
                shell.db.annotate_file_event(
                    matching.id,
                    git_commit=snap["commit_hash"],
                    lines_added=change["lines_added"],
                    lines_removed=change["lines_removed"],
                    source=matching.source,
                )
                continue
            shell.db.add_file_event(
                FileEvent(
                    folder_id=folder.id,
                    event_type=change["event_type"],
                    file_path=change["file_path"],
                    timestamp=commit_time,
                    git_commit=snap["commit_hash"],
                    lines_added=change["lines_added"],
                    lines_removed=change["lines_removed"],
                    is_directory=False,
                    source="argus_sync",
                )
            )

    folder_p = Path(folder.path)
    try:
        if folder_p.is_dir():
            _sync_folder_commits_to_events(shell, folder)
            snap = shell.git.snapshot(folder.path)
            if snap:
                record_snapshot(snap)
        else:
            snap = shell.git.snapshot(folder.path)
            if snap:
                record_snapshot(snap)
    except Exception as e:
        logger.debug("Argus live reconcile error: %s", e)


def _inspect_single_file_history(
    shell: SentienceShell,
    folder: TrackedFolder,
    rel_path: str = "",
) -> None:
    """Inspect full 45-day history, open durations, and exact line diffs for a single file."""
    retention_days = getattr(shell.config, "memory_retention_days", 45)
    since = datetime.now() - timedelta(days=retention_days)

    is_root_dir = Path(folder.path).is_dir()
    if is_root_dir and rel_path:
        target_abs = Path(folder.path) / rel_path
        display_name = rel_path
        sub_prompt_name = f"{Path(folder.path).name}/{Path(rel_path).name}"
    else:
        target_abs = Path(folder.path)
        display_name = target_abs.name
        sub_prompt_name = target_abs.name

    # Live reconcile changes against shadow git and DB
    reconcile_file_tracking(shell, folder, rel_path=rel_path)

    # Window sessions for duration correlation
    try:
        window_sessions = shell.db.get_window_sessions(since=since, limit=None)
    except Exception:
        window_sessions = []

    page_idx = 0
    page_size = 15

    while True:
        # Re-check live reconcile each loop in case of background updates
        reconcile_file_tracking(shell, folder, rel_path=rel_path)

        is_deleted = not target_abs.exists()

        # Retrieve git history commits
        git_commits = shell.git.file_history(
            folder.path,
            file_path=rel_path if rel_path else None,
            limit=None,
            since=since,
        )

        # Retrieve DB events
        db_events = shell.db.get_file_events(
            folder_id=folder.id,
            file_path=rel_path if rel_path else None,
            since=since,
            limit=None,
        )

        # Determine real file creation timestamp
        created_dt: Optional[datetime] = None
        if target_abs.exists():
            b_dt, _ = get_real_file_times(target_abs)
            if b_dt:
                created_dt = b_dt

        if git_commits:
            try:
                first_git_dt = to_naive_datetime(git_commits[-1]["timestamp"])
                if first_git_dt:
                    if not created_dt or first_git_dt < created_dt:
                        created_dt = first_git_dt
            except Exception:
                pass

        for ev in reversed(db_events):
            ev_dt = to_naive_datetime(ev.timestamp)
            if not ev_dt:
                continue
            if ev.event_type == FileEventType.CREATED:
                if not created_dt or ev_dt < created_dt:
                    created_dt = ev_dt
                break
            if not created_dt or ev_dt < created_dt:
                created_dt = ev_dt

        if not created_dt:
            created_dt = to_naive_datetime(folder.created_at) or datetime.now()

        created_str = created_dt.strftime("%Y-%m-%d %H:%M:%S")

        # Build chronological list of revisions
        revisions: List[Dict[str, Any]] = []
        seen_commits = set()

        # Parse git commits in chronological order (oldest to newest)
        chronological_commits = list(reversed(git_commits))
        prev_ts: Optional[datetime] = None

        # The filesystem event is Argus's observation time. Shadow Git commit
        # timestamps can be later than the actual edit (for example, when the
        # watcher reconciles a file after it was saved), so use the matching
        # event timestamp when one is available.
        event_time_by_commit: Dict[str, datetime] = {}
        for ev in db_events:  # get_file_events is newest-first
            ev_time = to_naive_datetime(ev.timestamp)
            if ev.git_commit and ev_time:
                event_time_by_commit.setdefault(ev.git_commit, ev_time)

        for commit_info in chronological_commits:
            c_hash = commit_info["commit"]
            seen_commits.add(c_hash)
            ts = event_time_by_commit.get(c_hash) or to_naive_datetime(commit_info["timestamp"]) or datetime.now()

            # Diff for this commit
            diff_text = shell.git.diff(
                folder.path,
                commit1=c_hash,
                file_path=rel_path if rel_path else None,
            )
            changes = parse_line_diffs(diff_text)
            added_cnt = sum(1 for c in changes if c["sign"] == "+")
            removed_cnt = sum(1 for c in changes if c["sign"] == "-")

            # Determine action
            msg = commit_info.get("message", "").lower()
            if "deleted file mode" in diff_text:
                action = FileEventType.DELETED
            elif "initial" in msg or not prev_ts:
                action = FileEventType.CREATED
            else:
                action = FileEventType.MODIFIED

            duration_str = estimate_open_duration(
                display_name, ts, window_sessions, prev_event_time=prev_ts
            )
            prev_ts = ts

            revisions.append({
                "commit": c_hash,
                "timestamp": ts,
                "action": action,
                "added": added_cnt,
                "removed": removed_cnt,
                "duration": duration_str,
                "changes": changes,
                "diff_text": diff_text,
                "message": commit_info.get("message", ""),
            })

        # Also incorporate any DB events without a git commit
        for ev in reversed(db_events):
            if ev.git_commit and ev.git_commit in seen_commits:
                continue
            ev_ts = to_naive_datetime(ev.timestamp) or datetime.now()
            duration_str = estimate_open_duration(
                display_name, ev_ts, window_sessions, prev_event_time=prev_ts
            )
            prev_ts = ev_ts
            revisions.append({
                "commit": ev.git_commit or "-",
                "timestamp": ev_ts,
                "action": ev.event_type,
                "added": ev.lines_added,
                "removed": ev.lines_removed,
                "duration": duration_str,
                "changes": [],
                "diff_text": "",
                "message": f"{ev.event_type.value} event",
            })

        # Fallback for newly created file on disk with no revisions yet
        if not revisions and target_abs.exists():
            try:
                content = target_abs.read_text(encoding="utf-8", errors="replace")
                init_lines = len(content.splitlines()) if content else 0
            except Exception:
                init_lines = 0

            init_changes = []
            if init_lines > 0:
                lines = target_abs.read_text(encoding="utf-8", errors="replace").splitlines()
                for i, l in enumerate(lines[:5], 1):
                    init_changes.append({"sign": "+", "line_no": i, "col": 1, "content": l})

            revisions.append({
                "commit": "-",
                "timestamp": created_dt,
                "action": FileEventType.CREATED,
                "added": init_lines,
                "removed": 0,
                "duration": "-",
                "changes": init_changes,
                "diff_text": "",
                "message": "File created",
            })

        # Sort all revisions chronologically
        revisions.sort(key=lambda x: x["timestamp"])

        # If file is deleted on disk but last revision is not DELETED, record/show DELETED status
        if is_deleted:
            if not revisions or revisions[-1]["action"] != FileEventType.DELETED:
                del_time = datetime.now()
                revisions.append({
                    "commit": "-",
                    "timestamp": del_time,
                    "action": FileEventType.DELETED,
                    "added": 0,
                    "removed": 0,
                    "duration": "-",
                    "changes": [],
                    "diff_text": "",
                    "message": "File deleted from disk",
                })

        # Compute specific incremental time range and duration for each revision
        for i, rev in enumerate(revisions):
            prev_r_ts = revisions[i - 1]["timestamp"] if i > 0 else None
            is_cre = (rev["action"] == FileEventType.CREATED or i == 0)
            tr_str, d_str = get_window_tracking_info(
                display_name,
                rev["timestamp"],
                window_sessions,
                prev_event_time=prev_r_ts,
                is_created=is_cre,
            )
            rev["time_range"] = tr_str
            rev["duration_display"] = d_str

        total_added = sum(r["added"] for r in revisions)
        total_removed = sum(r["removed"] for r in revisions)
        status_banner = (
            "[bold red]✖ FILE DELETED[/bold red]"
            if is_deleted
            else "[bold green]● Active on disk[/bold green]"
        )

        console.print()
        console.print(Panel(
            f"[bold white]File History & Activity — {display_name}[/bold white]\n"
            f"[dim]Full Path:[/dim] [bold cyan]{target_abs}[/bold cyan]\n"
            f"[dim]File Created:[/dim] [yellow]{created_str}[/yellow]   "
            f"[dim]Window:[/dim] [green]Last {retention_days} Days[/green]   "
            f"[dim]Status:[/dim] {status_banner}\n"
            f"[dim]Total Revisions:[/dim] [bold white]{len(revisions)}[/bold white]   "
            f"[dim]Overall Line Variations:[/dim] [green]+{total_added}[/green] / [red]-{total_removed}[/red]",
            border_style="red" if is_deleted else "green",
        ))

        total_pages = max(1, (len(revisions) + page_size - 1) // page_size)
        if page_idx >= total_pages:
            page_idx = total_pages - 1
        if page_idx < 0:
            page_idx = 0

        start_idx = page_idx * page_size
        end_idx = min(start_idx + page_size, len(revisions))
        page_revisions = revisions[start_idx:end_idx]

        if not revisions:
            console.print("[dim yellow]No recorded modification events for this file in the last 45 days.[/dim yellow]")
        else:
            page_title = (
                f"[bold green]Revisions Timeline for {display_name}[/bold green]"
                if total_pages == 1
                else f"[bold green]Revisions Timeline for {display_name} (Page {page_idx + 1}/{total_pages})[/bold green]"
            )
            table = Table(
                title=page_title,
                show_header=True,
                header_style="bold cyan",
                padding=(0, 1),
            )
            table.add_column("#", justify="right", style="bold yellow", width=5)
            table.add_column("Change Recorded At", justify="center", style="bold cyan", width=21, no_wrap=False)
            table.add_column("Recorded Editor Session", justify="center", style="bold green", width=34, no_wrap=False)
            table.add_column("Action", width=14)
            table.add_column("Changes (+/-)", justify="right", width=15)
            table.add_column("Exact Line Diff (Line No, Col & Code)", min_width=32, ratio=2, overflow="fold")
            table.add_column("Commit", style="dim cyan", width=10)

            for idx_offset, rev in enumerate(page_revisions):
                actual_idx = start_idx + idx_offset + 1
                # Keep the file-change timestamp distinct from the editor
                # session intervals used to calculate observed foreground time.
                time_range_str = rev["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
                dur_str = format_recorded_editor_time(
                    rev.get("time_range", ""), rev.get("duration_display", "-")
                )
                commit_short = rev["commit"][:8] if rev["commit"] != "-" else "-"
                action_badge = _format_event_badge(rev["action"])
                changes_str = f"[green]+{rev['added']}[/green] / [red]-{rev['removed']}[/red]"
                if not rev["changes"] and rev["action"] == FileEventType.CREATED:
                    diff_col = "[dim]Empty file created (0 lines)[/dim]"
                else:
                    diff_col = format_diff_snippet(rev["changes"])

                table.add_row(
                    str(actual_idx),
                    time_range_str,
                    dur_str,
                    action_badge,
                    changes_str,
                    diff_col,
                    commit_short,
                )
            console.print(table)

            if is_deleted:
                console.print(Panel(
                    "[bold red]⚠️ File deleted[/bold red]\n"
                    "[red]This file no longer exists at its tracked location on disk.[/red]",
                    border_style="red",
                ))

        console.print()
        if total_pages > 1:
            console.print(
                "[dim]'n'/'p' to page · '<#>' for diff · 'cat' to view · 'back' to return[/dim]"
            )
        else:
            console.print(
                "[dim]'<#>' for diff · 'cat' to view · 'back' to return[/dim]"
            )

        cmd = pt_prompt(f"argus [{sub_prompt_name}] > ").strip().lower()
        if cmd in ("back", "exit", "quit", "q", "b", ":q", ""):
            break

        if cmd in ("n", "next"):
            if page_idx < total_pages - 1:
                page_idx += 1
            else:
                console.print("[dim yellow]Already on the last page.[/dim yellow]")
        elif cmd in ("p", "prev", "previous"):
            if page_idx > 0:
                page_idx -= 1
            else:
                console.print("[dim yellow]Already on the first page.[/dim yellow]")
        elif cmd in ("cat", "c", "view", "content"):
            if target_abs.exists():
                try:
                    content = target_abs.read_text(encoding="utf-8", errors="replace")
                    ext = target_abs.suffix.lstrip(".") or "text"
                    console.print(Panel(
                        Syntax(content, ext, line_numbers=True, theme="monokai", word_wrap=True),
                        title=f"[bold green]Current Content — {display_name}[/bold green]",
                        border_style="green",
                    ))
                except Exception as e:
                    console.print(f"[red]Error reading file:[/red] {e}")
            else:
                console.print(Panel(
                    "[bold red]File deleted[/bold red]\n"
                    "[dim red]Cannot view content because the file does not exist on disk.[/dim red]",
                    border_style="red",
                ))
        elif cmd.isdigit():
            rev_num = int(cmd)
            if 1 <= rev_num <= len(revisions):
                target_rev = revisions[rev_num - 1]
                diff_to_show = target_rev["diff_text"]
                if not diff_to_show and target_rev["commit"] != "-":
                    diff_to_show = shell.git.diff(
                        folder.path,
                        commit1=target_rev["commit"],
                        file_path=rel_path if rel_path else None,
                    )
                date_fmt = target_rev["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
                render_diff_view(
                    diff_to_show,
                    f"Revision #{rev_num} ({target_rev['action'].value}) — {date_fmt}",
                )
            else:
                console.print(f"[red]Invalid revision #{rev_num}. Valid range is 1 to {len(revisions)}[/red]")


def _inspect_folder_history(shell: SentienceShell, folder: TrackedFolder) -> None:
    """Inspect folder changes: lists all changed/created files and allows picking any file."""
    retention_days = getattr(shell.config, "memory_retention_days", 45)
    since = datetime.now() - timedelta(days=retention_days)
    folder_p = Path(folder.path)
    folder_name = folder_p.name or str(folder.path)

    # Take fresh snapshot to ensure all changes are synced
    reconcile_file_tracking(shell, folder)

    while True:
        reconcile_file_tracking(shell, folder)

        all_events = shell.db.get_file_events(
            folder_id=folder.id,
            since=since,
            limit=None,
        )

        by_file: Dict[str, Dict[str, Any]] = {}
        for ev in all_events:
            fp = ev.file_path
            if (
                fp == folder_p.name
                or fp in ("", ".")
                or any(part.startswith(".") for part in Path(fp).parts)
                or (folder_p / fp).is_dir()
            ):
                continue
            ev_ts = to_naive_datetime(ev.timestamp) or datetime.now()
            if fp not in by_file:
                by_file[fp] = {
                    "path": fp,
                    "first_seen": ev_ts,
                    "last_modified": ev_ts,
                    "count": 0,
                    "latest_event": ev.event_type,
                    "added": 0,
                    "removed": 0,
                    "has_modified": False,
                }
            item = by_file[fp]
            item["count"] += 1
            item["added"] += ev.lines_added
            item["removed"] += ev.lines_removed
            if ev.event_type == FileEventType.MODIFIED:
                item["has_modified"] = True
            if ev_ts < item["first_seen"]:
                item["first_seen"] = ev_ts
            if ev_ts > item["last_modified"]:
                item["last_modified"] = ev_ts
                item["latest_event"] = ev.event_type

        # Also inspect disk files and reconcile real filesystem timestamps
        if folder_p.is_dir():
            try:
                disk_entries = [
                    entry
                    for entry in folder_p.rglob("*")
                    if entry.is_file() and not any(part.startswith(".") for part in entry.parts)
                ]
                times = get_batch_file_times(disk_entries)

                for entry in disk_entries:
                    rel = str(entry.relative_to(folder_p))
                    b_dt, m_dt = times.get(str(entry), (None, None))
                    if not b_dt or not m_dt:
                        try:
                            st = entry.stat()
                            if not m_dt:
                                m_dt = to_naive_datetime(st.st_mtime) or datetime.now()
                            if not b_dt:
                                b = getattr(st, "st_birthtime", None)
                                b_dt = to_naive_datetime(b if b else st.st_ctime) or m_dt
                        except Exception:
                            pass

                    try:
                        content = entry.read_text(encoding="utf-8", errors="replace")
                        line_count = len(content.splitlines()) if content else 0
                    except Exception:
                        line_count = 0

                    if rel not in by_file:
                        by_file[rel] = {
                            "path": rel,
                            "first_seen": b_dt or m_dt or datetime.now(),
                            "last_modified": m_dt or datetime.now(),
                            "count": 1,
                            "latest_event": FileEventType.CREATED,
                            "added": line_count,
                            "removed": 0,
                            "has_modified": False,
                        }
                    else:
                        item = by_file[rel]
                        if b_dt and b_dt < item["first_seen"]:
                            item["first_seen"] = b_dt
                        if not item.get("has_modified") and m_dt:
                            item["last_modified"] = m_dt
                            if b_dt:
                                item["first_seen"] = b_dt
                        elif m_dt and m_dt > item["last_modified"]:
                            item["last_modified"] = m_dt
            except Exception:
                pass

        file_list = sorted(by_file.values(), key=lambda x: x["last_modified"], reverse=True)

        console.print()
        console.print(Panel(
            f"[bold white]Argus Folder Activity — {folder_name}[/bold white]\n"
            f"[dim]Directory Path:[/dim] [bold cyan]{folder.path}[/bold cyan]\n"
            f"[dim]Tracking Duration:[/dim] [yellow]{format_duration(folder.created_at)}[/yellow]   "
            f"[dim]History Window:[/dim] [green]Last {retention_days} Days[/green]\n"
            f"[dim]Active / Changed Files:[/dim] [bold white]{len(file_list)}[/bold white]   "
            f"[dim]Total Event Log Entries:[/dim] [bold white]{len(all_events)}[/bold white]",
            border_style="green",
        ))

        if not file_list:
            console.print("[dim yellow]No file activity or changes recorded in this folder yet.[/dim yellow]")
        else:
            table = Table(
                title=f"[bold green]Tracked Files in {folder_name}[/bold green]",
                show_header=True,
                header_style="bold cyan",
                padding=(0, 1),
            )
            table.add_column("#", justify="right", style="bold yellow", width=5)
            table.add_column("Relative File Path", style="bold white", min_width=24, ratio=2, overflow="fold")
            table.add_column("Created / First Seen", style="dim white", width=19)
            table.add_column("Last Modified", style="cyan", width=19)
            table.add_column("Changes", justify="right", width=9)
            table.add_column("Latest Status", width=14)
            table.add_column("Lines (+ / -)", justify="right", width=16)

            for idx, item in enumerate(file_list, 1):
                created_str = item["first_seen"].strftime("%Y-%m-%d %H:%M")
                mod_str = item["last_modified"].strftime("%Y-%m-%d %H:%M")
                # Check if deleted on disk or modified
                target_f = folder_p / item["path"]
                if not target_f.exists():
                    latest_status = FileEventType.DELETED
                elif item.get("has_modified") or item["latest_event"] == FileEventType.MODIFIED:
                    latest_status = FileEventType.MODIFIED
                else:
                    latest_status = FileEventType.CREATED

                status_badge = _format_event_badge(latest_status)

                table.add_row(
                    str(idx),
                    item["path"],
                    created_str,
                    mod_str,
                    str(item["count"]),
                    status_badge,
                    f"[green]+{item['added']}[/green] / [red]-{item['removed']}[/red]",
                )
            console.print(table)

        console.print()
        console.print("[dim]Enter a file # to inspect · 'back' to return[/dim]")
        choice = pt_prompt(f"argus [{folder_name}] (Enter file # or 'back') > ").strip().lower()
        if choice in ("back", "exit", "quit", "q", "b", ":q", ""):
            break

        try:
            f_num = int(choice)
            if 1 <= f_num <= len(file_list):
                chosen_target = file_list[f_num - 1]
                _inspect_single_file_history(shell, folder, rel_path=chosen_target["path"])
            else:
                console.print(f"[red]Invalid file #{f_num}. Valid range is 1 to {len(file_list)}[/red]")
        except ValueError:
            console.print("[red]Please enter a valid numeric file # or 'back'.[/red]")


def argus_command(shell: SentienceShell, args: List[str]) -> None:
    """Main entrypoint for Argus watching and change inspection feature."""
    init_cmd = args[0].lower() if args else None

    while True:
        folders = shell.db.get_all_folders()

        if not init_cmd:
            console.print()
            console.print(Panel(
                "[bold white]Argus — Filesystem Activity & Change Inspector[/bold white]\n"
                "[dim]Monitor real-time file & folder modifications, line diffs, and 45-day revision history[/dim]",
                border_style="green",
            ))

            if not folders:
                console.print("[dim yellow]No folders or files are currently being tracked.[/dim yellow]")
                console.print("[dim]Use the 'archive' command to add directories or files to track first.[/dim]\n")
                choice = pt_prompt("argus > ").strip().lower()
                if choice in ("back", "exit", "quit", "q", "b", ":q", ""):
                    break
                continue

            console.print(build_argus_tracked_table(folders))
            console.print()
            console.print("[dim]Enter S.No to inspect history · 'back' to return[/dim]")
            choice = pt_prompt("argus (Enter S.No to inspect history or 'back') > ").strip().lower()
        else:
            choice = init_cmd
            init_cmd = None

        if choice in ("back", "exit", "quit", "q", "b", ":q", ""):
            break

        try:
            sno = int(choice)
            if 1 <= sno <= len(folders):
                target_folder = folders[sno - 1]
                is_dir = Path(target_folder.path).is_dir()
                if is_dir:
                    _inspect_folder_history(shell, target_folder)
                else:
                    _inspect_single_file_history(shell, target_folder)
            else:
                console.print(f"[red]Invalid S.No {sno}. Valid range is 1 to {len(folders)}[/red]")
        except ValueError:
            console.print("[red]Please enter a valid numeric S.No or 'back'.[/red]")
