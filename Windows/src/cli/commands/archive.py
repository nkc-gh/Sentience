"""
SENTIENCE Archive Command (formerly Directory).

Manages tracked roots (files/folders) with precise permission controls:
Track, Read, Edit, Index, History.
Prompt stays on selected options/sub-modes until explicit 'back' instruction.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from cli.prompts import prompt as pt_prompt
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from storage.models import PrivacyLabel, TrackedFolder

if TYPE_CHECKING:
    from cli.shell import SentienceShell

console = Console()

PERMISSIONS_HELP_DATA = [
    ("Track", "Monitor filesystem events and record when files are created, modified, renamed, or deleted."),
    ("Read", "Read file contents when needed to answer questions or perform tasks."),
    ("Edit", "Create, modify, rename, and delete files, subject to the configured action permissions."),
    ("Index", "Process file contents for local search and retrieval-augmented generation (RAG)."),
    ("History", "Store and retrieve snapshots, diffs, and previous versions, where captured."),
]


def render_permissions_help_table() -> Table:
    """Render the permissions reference table."""
    table = Table(
        title="[bold cyan]Filesystem Permissions Reference[/bold cyan]",
        box=None,
        show_header=True,
        header_style="bold green",
        padding=(0, 2),
    )
    table.add_column("Permission", style="bold yellow", width=12)
    table.add_column("What it allows", style="dim white")

    for perm, desc in PERMISSIONS_HELP_DATA:
        table.add_row(perm, desc)
    return table


def get_path_item_type(path_str: str) -> str:
    """Return whether path is a Folder or a File."""
    p = Path(path_str)
    if p.is_dir():
        return "Folder"
    elif p.is_file():
        return "File"
    elif p.suffix:
        return "File"
    return "Folder"


def launch_gui_picker(mode: str = "folder") -> Optional[str]:
    """
    Launch a native desktop GUI file or folder picker.
    mode: 'folder', 'file', or 'json'
    """
    if os.name == "nt":
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            if mode == "folder":
                selected = filedialog.askdirectory(title="Select Folder to Track in Sentience")
            elif mode == "json":
                selected = filedialog.askopenfilename(
                    title="Select Google OAuth credentials JSON",
                    filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
                )
            else:
                selected = filedialog.askopenfilename(title="Select File to Track in Sentience")
            root.destroy()
            return selected or None
        except Exception:
            return None

    # 1. Zenity (GNOME / GTK / Standard Linux)
    if shutil.which("zenity"):
        cmd = ["zenity", "--file-selection"]
        if mode == "folder":
            cmd.extend(["--directory", "--title=Select Folder to Track in Sentience"])
        elif mode == "json":
            cmd.extend(["--title=Select Google OAuth credentials JSON", "--file-filter=JSON files | *.json"])
        else:
            cmd.append("--title=Select File to Track in Sentience")
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
            return None
        except Exception:
            pass

    # 2. Kdialog (KDE Plasma)
    if shutil.which("kdialog"):
        if mode == "folder":
            cmd = ["kdialog", "--getexistingdirectory", "--title", "Select Folder to Track in Sentience"]
        elif mode == "json":
            cmd = ["kdialog", "--getopenfilename", "", "*.json", "--title", "Select Google OAuth credentials JSON"]
        else:
            cmd = ["kdialog", "--getopenfilename", "--title", "Select File to Track in Sentience"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
            return None
        except Exception:
            pass

    # 3. YAD (Yet Another Dialog)
    if shutil.which("yad"):
        cmd = ["yad", "--file"]
        if mode == "folder":
            cmd.extend(["--directory", "--title=Select Folder to Track in Sentience"])
        elif mode == "json":
            cmd.extend(["--title=Select Google OAuth credentials JSON", "--file-filter=JSON files | *.json"])
        else:
            cmd.append("--title=Select File to Track in Sentience")
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
            return None
        except Exception:
            pass

    return None


def format_duration(start_time: datetime) -> str:
    """Format duration into days, hours, minutes, seconds."""
    now = datetime.now()
    delta = now - start_time
    total_seconds = max(0, int(delta.total_seconds()))

    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    date_str = start_time.strftime("%d %b %Y")
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0 or days > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or hours > 0 or days > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")

    duration_str = " ".join(parts)
    return f"Since {date_str} ({duration_str})"


def format_access_badges(folder: TrackedFolder) -> str:
    badges = []
    badges.append("[green]Track[/green]" if folder.track_allowed else "[red]Track[/red]")
    badges.append("[green]Read[/green]" if folder.read_allowed else "[red]Read[/red]")
    badges.append("[green]Edit[/green]" if folder.edit_allowed else "[red]Edit[/red]")
    badges.append("[green]Index[/green]" if folder.index_allowed else "[red]Index[/red]")
    badges.append("[green]History[/green]" if folder.history_allowed else "[red]History[/red]")
    return " [dim]|[/dim] ".join(badges)


def build_archive_table(folders: List[TrackedFolder]) -> Table:
    table = Table(
        title="[bold green]Archive Tracked Items[/bold green]",
        show_header=True,
        header_style="bold cyan",
        padding=(0, 1),
    )
    table.add_column("S.No", justify="right", style="bold yellow", width=6)
    table.add_column("Name", style="bold white", width=18)
    table.add_column("Path", style="dim", min_width=32, ratio=2, overflow="fold")
    table.add_column("Type", width=12)
    table.add_column("Access", width=46)
    table.add_column("Tracking Duration", style="cyan", width=28)

    for idx, folder in enumerate(folders, 1):
        p = Path(folder.path)
        name = p.name or str(p)
        item_type = get_path_item_type(folder.path)
        type_str = f"[blue]📁 Folder[/blue]" if item_type == "Folder" else f"[yellow]📄 File[/yellow]"
        duration_str = format_duration(folder.created_at)
        access_str = format_access_badges(folder)

        table.add_row(
            str(idx),
            name,
            str(folder.path),
            type_str,
            access_str,
            duration_str,
        )
    return table


def _edit_folder_permissions(shell: SentienceShell, folder: TrackedFolder) -> None:
    """Interactively adjust permissions for an existing tracked item and sync watchers."""
    item_type = get_path_item_type(folder.path)
    type_badge = "[blue]📁 Folder[/blue]" if item_type == "Folder" else "[yellow]📄 File[/yellow]"
    console.print(f"\n[bold cyan]Editing permissions for {type_badge}:[/bold cyan] [bold white]{folder.path}[/bold white]")
    console.print("[dim]Press Enter to keep current setting, or type 'y' / 'n'.[/dim]\n")

    t_in = pt_prompt(f"Allow Track (filesystem monitoring)? [Y/n] (current: {'Yes' if folder.track_allowed else 'No'}): ").strip().lower()
    track_allowed = (t_in != "n") if t_in else folder.track_allowed

    r_in = pt_prompt(f"Allow Read (file content reading)? [Y/n] (current: {'Yes' if folder.read_allowed else 'No'}): ").strip().lower()
    read_allowed = (r_in != "n") if r_in else folder.read_allowed

    e_in = pt_prompt(f"Allow Edit (file modification)? [y/N] (current: {'Yes' if folder.edit_allowed else 'No'}): ").strip().lower()
    edit_allowed = (e_in == "y") if e_in else folder.edit_allowed

    i_in = pt_prompt(f"Allow Index (local search / RAG)? [Y/n] (current: {'Yes' if folder.index_allowed else 'No'}): ").strip().lower()
    index_allowed = (i_in != "n") if i_in else folder.index_allowed

    h_in = pt_prompt(f"Allow History (snapshots / diffs)? [Y/n] (current: {'Yes' if folder.history_allowed else 'No'}): ").strip().lower()
    history_allowed = (h_in != "n") if h_in else folder.history_allowed

    model_scope = getattr(folder, "model_scope", "hybrid") or "hybrid"

    folder.track_allowed = track_allowed
    folder.read_allowed = read_allowed
    folder.edit_allowed = edit_allowed
    folder.index_allowed = index_allowed
    folder.history_allowed = history_allowed
    folder.model_scope = model_scope

    if folder.id:
        shell.db.update_folder_permissions(
            folder.id, track_allowed, read_allowed, edit_allowed, index_allowed, history_allowed, model_scope
        )

    # Synchronize filesystem collector & tracking state
    if track_allowed:
        if folder.id and folder.id not in shell.fs_collector.watching:
            shell.fs_collector.start_watching(folder.id, folder.path)
            try:
                shell.git.init_repo(folder.path)
            except Exception:
                pass
            shell.db.set_tracking_state("on")
    else:
        if folder.id and folder.id in shell.fs_collector.watching:
            shell.fs_collector.stop_watching(folder.id)
            if not shell.fs_collector.watching:
                shell.db.set_tracking_state("off")

    console.print(f"\n[bold green]✓ Successfully updated permissions for:[/bold green] {folder.path}\n")


def _sub_list_loop(shell: SentienceShell) -> None:
    """Stay inside archive [list] until user inputs 'back'."""
    while True:
        folders = shell.db.get_all_folders()
        if not folders:
            console.print("\n[dim yellow]No folders or files are currently being tracked.[/dim yellow]")
            console.print("[dim]Use option 2 (Add new tracking) to begin tracking a directory or file.[/dim]\n")
        else:
            console.print()
            console.print(build_archive_table(folders))
            console.print()

        cmd = pt_prompt("archive [list] > ").strip().lower()
        if cmd in ("back", "exit", "quit", "q", "b", ":q"):
            break


def _sub_add_loop(shell: SentienceShell) -> None:
    """Stay inside archive [add] until user inputs 'back'."""
    while True:
        console.print()
        console.print(Panel(
            "[bold white]Add New Tracking[/bold white]\n"
            "  [bold yellow]1[/bold yellow]. [bold white]Add Path[/bold white]   [dim yellow](Type absolute file or folder path directly)[/dim yellow]\n"
            "  [bold yellow]2[/bold yellow]. [bold white]GUI Picker[/bold white] [dim](Native desktop file / folder selection dialog)[/dim]\n"
            "[dim]Type 1, 2 or 'back' to return to archive menu[/dim]",
            border_style="green",
        ))

        method = pt_prompt("archive [add] > ").strip().lower()
        if method in ("back", "exit", "quit", "q", "b", ""):
            break

        target_path: str = ""
        folders = shell.db.get_all_folders()

        if method in ("1", "path"):
            console.print("[dim]Enter absolute path (e.g. /home/user/project or /home/user/notes.txt):[/dim]")
            entered = pt_prompt("absolute path > ").strip()
            if entered.lower() in ("back", "q", ""):
                continue
            target_path = os.path.abspath(os.path.expanduser(entered))
        elif method in ("2", "picker", "gui"):
            console.print("\n[bold cyan]Choose GUI Picker type:[/bold cyan]")
            console.print("  [bold yellow]1[/bold yellow]. [bold white]Folder[/bold white] [dim]— open desktop dialog to select a directory[/dim]")
            console.print("  [bold yellow]2[/bold yellow]. [bold white]File[/bold white]   [dim]— open desktop dialog to select a single file[/dim]")
            console.print("[dim]Enter 1 or 2 (Press Enter for Folder, 'back' to cancel)[/dim]")
            g_choice = pt_prompt("picker target [1/2] > ").strip().lower()
            if g_choice in ("back", "q", "b"):
                continue
            pick_mode = "file" if g_choice in ("2", "file", "f") else "folder"
            console.print(f"[dim]Opening desktop GUI picker for {pick_mode}...[/dim]")
            picked = launch_gui_picker(pick_mode)
            if not picked:
                console.print("[dim yellow]GUI picker cancelled or no selection made.[/dim yellow]")
                continue
            target_path = os.path.abspath(os.path.expanduser(picked))
        else:
            continue

        if not target_path or not os.path.exists(target_path):
            console.print(f"[red]Error:[/red] Path does not exist on disk: {target_path}")
            continue

        if any(f.path == target_path for f in folders):
            console.print(f"[yellow]Path is already tracked:[/yellow] {target_path}")
            continue

        # Inspect if picked path is a file or a folder and notify user
        item_type = get_path_item_type(target_path)
        type_badge = "[blue]📁 Folder[/blue]" if item_type == "Folder" else "[yellow]📄 File[/yellow]"
        console.print(f"\n[bold green]✓ Picked {type_badge}:[/bold green] [bold cyan]{target_path}[/bold cyan]")
        console.print(f"[dim]Configuring permissions for {item_type.lower()}:[/dim] [bold]{target_path}[/bold]")

        track_in = pt_prompt("Allow Track (monitoring filesystem)? (Y/n): ").strip().lower()
        track_allowed = track_in != "n"

        read_in = pt_prompt("Allow Read (file content reading)? (Y/n): ").strip().lower()
        read_allowed = read_in != "n"

        edit_in = pt_prompt("Allow Edit (file modification)? (y/N): ").strip().lower()
        edit_allowed = edit_in == "y"

        index_in = pt_prompt("Allow Index (local search / RAG)? (Y/n): ").strip().lower()
        index_allowed = index_in != "n"

        hist_in = pt_prompt("Allow History (snapshots / diffs)? (Y/n): ").strip().lower()
        history_allowed = hist_in != "n"

        model_scope = "hybrid"

        new_folder = TrackedFolder(
            path=target_path,
            label=PrivacyLabel.PUBLIC,
            track_allowed=track_allowed,
            read_allowed=read_allowed,
            edit_allowed=edit_allowed,
            index_allowed=index_allowed,
            history_allowed=history_allowed,
            model_scope=model_scope,
        )
        saved = shell.db.add_folder(new_folder)
        console.print(f"\n[bold green]✓ Successfully added {type_badge} to archive tracking (ID: {saved.id}):[/bold green] {target_path}")

        # Activate filesystem watching immediately if track_allowed
        if saved.track_allowed and saved.id:
            shell.fs_collector.start_watching(saved.id, saved.path)
            shell.db.set_tracking_state("on")
            try:
                shell.git.init_repo(saved.path)
                from cli.commands.argus import reconcile_file_tracking

                reconcile_file_tracking(shell, saved)
            except Exception:
                pass


def _sub_edit_loop(shell: SentienceShell) -> None:
    """Stay inside archive [edit] until user inputs 'back'."""
    while True:
        folders = shell.db.get_all_folders()
        if not folders:
            console.print("\n[dim yellow]No folders or files are currently being tracked to edit.[/dim yellow]\n")
            cmd = pt_prompt("archive [edit] > ").strip().lower()
            if cmd in ("back", "exit", "quit", "q", "b", ""):
                break
            continue

        console.print()
        console.print(build_archive_table(folders))
        console.print()

        sno_in = pt_prompt("archive [edit] (Enter S.No to edit permissions or 'back') > ").strip()
        if sno_in.lower() in ("back", "exit", "quit", "q", "b", ""):
            break

        try:
            sno = int(sno_in)
            if 1 <= sno <= len(folders):
                target = folders[sno - 1]
                _edit_folder_permissions(shell, target)
            else:
                console.print(f"[red]Invalid S.No {sno}. Valid range is 1 to {len(folders)}[/red]")
        except ValueError:
            console.print("[red]Please enter a valid numeric S.No.[/red]")


def _sub_remove_loop(shell: SentienceShell) -> None:
    """Stay inside archive [remove] until user inputs 'back', offering edit permission option."""
    while True:
        folders = shell.db.get_all_folders()
        if not folders:
            console.print("\n[dim yellow]No folders or files are currently being tracked.[/dim yellow]\n")
            cmd = pt_prompt("archive [remove] > ").strip().lower()
            if cmd in ("back", "exit", "quit", "q", "b", ""):
                break
            continue

        console.print()
        console.print(build_archive_table(folders))
        console.print()

        sno_in = pt_prompt("archive [remove] (Enter S.No to remove/edit or 'back') > ").strip()
        if sno_in.lower() in ("back", "exit", "quit", "q", "b", ""):
            break

        try:
            sno = int(sno_in)
            if 1 <= sno <= len(folders):
                target = folders[sno - 1]
                action = pt_prompt(
                    f"Action for '{target.path}': (r: remove / e: edit permissions / N: cancel): "
                ).strip().lower()

                if action in ("r", "remove", "y", "yes"):
                    if target.id:
                        shell.fs_collector.stop_watching(target.id)
                        shell.db.remove_folder(target.id)
                        if not shell.fs_collector.watching:
                            shell.db.set_tracking_state("off")
                    console.print(f"\n[bold green]✓ Removed tracked item #{sno}:[/bold green] {target.path}\n")
                elif action in ("e", "edit", "permission", "permissions"):
                    _edit_folder_permissions(shell, target)
                else:
                    console.print("[dim]Cancelled.[/dim]")
            else:
                console.print(f"[red]Invalid S.No {sno}. Valid range is 1 to {len(folders)}[/red]")
        except ValueError:
            console.print("[red]Please enter a valid numeric S.No.[/red]")


def run_archive_command(shell: SentienceShell, args: List[str]) -> None:
    """Interactive archive management command with 4 options and persistent sub-mode loops."""
    init_cmd = args[0].lower() if args else None

    while True:
        if not init_cmd:
            console.print()
            console.print(render_permissions_help_table())
            console.print()
            console.print(Panel(
                "[bold white]Archive Management[/bold white]\n"
                "[dim]Manage filesystem roots, monitoring, and granular access permissions[/dim]\n\n"
                "  [bold yellow]1[/bold yellow]. [bold white]List tracked items[/bold white] (view tracked files/folders, duration & access)\n"
                "  [bold yellow]2[/bold yellow]. [bold white]Add new tracking[/bold white]   (specify absolute path or GUI desktop picker)\n"
                "  [bold yellow]3[/bold yellow]. [bold white]Edit permissions[/bold white]   (modify Track, Read, Edit, Index, History permissions)\n"
                "  [bold yellow]4[/bold yellow]. [bold white]Remove tracking[/bold white]    (remove by S.No or edit permissions instead)\n\n"
                "[dim]Type 1, 2, 3, 4 or 'back' to return to sentience[/dim]",
                border_style="green",
            ))

            choice = pt_prompt("archive > ").strip().lower()
        else:
            choice = init_cmd
            init_cmd = None

        if choice in ("back", "exit", "quit", "q", "b", ":q", ""):
            break

        if choice in ("1", "list"):
            _sub_list_loop(shell)
        elif choice in ("2", "add", "new"):
            _sub_add_loop(shell)
        elif choice in ("3", "edit", "perm", "permission", "permissions"):
            _sub_edit_loop(shell)
        elif choice in ("4", "remove", "delete", "rm"):
            _sub_remove_loop(shell)
        else:
            console.print(f"[dim]Unknown option '{choice}'. Choose 1, 2, 3, 4 or 'back'.[/dim]")
