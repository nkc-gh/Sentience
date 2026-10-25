"""
SENTIENCE Chats Command.

Manages conversational sessions:
- Show: Display all active chats with contiguous ID, chat name, context window numbers (12k/200k), and stacked models.
- Delete: Delete chat by displayed ID number, with automatic contiguous list re-indexing.
Prompt stays in sub-modes until explicit 'back' instruction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List

from cli.prompts import prompt as pt_prompt
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

if TYPE_CHECKING:
    from cli.shell import SentienceShell

console = Console()


def render_chats_table(chats: list) -> Table:
    """Render chats table with contiguous 1-based IDs."""
    table = Table(
        title="[bold green]Conversations & Chat Sessions[/bold green]",
        show_header=True,
        header_style="bold cyan",
        padding=(0, 1),
    )
    table.add_column("ID", justify="right", style="bold yellow", width=6)
    table.add_column("Chat Name", style="bold white", width=28)
    table.add_column("Models", style="magenta", width=36)
    table.add_column("Last Active", style="dim", width=20)

    for idx, c in enumerate(chats, 1):
        models = getattr(c, "models_used", [])
        if models:
            models_str = " + ".join(models)
        else:
            models_str = "[dim]Standard Assistant[/dim]"

        updated_str = c.updated_at.strftime("%Y-%m-%d %H:%M") if hasattr(c, "updated_at") else ""

        table.add_row(
            str(idx),
            c.name,
            models_str,
            updated_str,
        )
    return table


def _sub_show_loop(shell: SentienceShell) -> None:
    """Stay inside chats [show] until user inputs 'back'."""
    while True:
        chats = shell.db.get_chats()
        if not chats:
            console.print("\n[dim yellow]No active chats found.[/dim yellow]")
            console.print("[dim]Use the 'agent' command to start a new chat session.[/dim]\n")
        else:
            console.print()
            console.print(render_chats_table(chats))
            console.print()

        cmd = pt_prompt("chats [show] > ").strip().lower()
        if cmd in ("back", "exit", "quit", "q", "b", ":q"):
            break


def _sub_delete_loop(shell: SentienceShell) -> None:
    """Stay inside chats [delete] until user inputs 'back'."""
    while True:
        chats = shell.db.get_chats()
        if not chats:
            console.print("\n[dim yellow]No active chats to delete.[/dim yellow]\n")
            cmd = pt_prompt("chats [delete] > ").strip().lower()
            if cmd in ("back", "exit", "quit", "q", "b", ""):
                break
            continue

        console.print()
        console.print(render_chats_table(chats))
        console.print()

        choice_str = pt_prompt("chats [delete] (Chat ID(s), comma-separated, or 'back') > ").strip()
        if choice_str.lower() in ("back", "exit", "quit", "q", "b", ""):
            break

        try:
            selected_ids = list(dict.fromkeys(int(value.strip()) for value in choice_str.split(",")))
            if not selected_ids or any(not 1 <= index <= len(chats) for index in selected_ids):
                raise ValueError
        except ValueError:
            console.print(f"[red]Enter valid chat IDs from 1 to {len(chats)}, separated by commas.[/red]")
            continue

        selected = [(index, chats[index - 1]) for index in selected_ids]
        labels = ", ".join(f"#{index} '{chat.name}'" for index, chat in selected)
        confirm = pt_prompt(f"Delete {labels}? (y/N): ").strip().lower()
        if confirm == "y":
            for _index, target_chat in selected:
                if target_chat.id is not None:
                    shell.db.delete_chat(target_chat.id)
            console.print(f"[bold green]✓ Deleted {len(selected)} chat(s):[/bold green] {labels}")
        else:
            console.print("[dim]Deletion cancelled.[/dim]")


def chats_command(shell: SentienceShell, args: List[str]) -> None:
    """Manage chats with persistent sub-mode loops."""
    init_cmd = args[0].lower() if args else None

    while True:
        if not init_cmd:
            console.print()
            console.print(Panel(
                "[bold white]Chat Management[/bold white]\n"
                "[dim]Manage unified conversation state and model stacks[/dim]\n\n"
                "  [bold yellow]1[/bold yellow]. [bold white]Show[/bold white] (view all active chats with ID, name, and models)\n"
                "  [bold yellow]2[/bold yellow]. [bold white]Delete[/bold white] (one chat or comma-separated IDs)\n\n"
                "[dim]Type 1, 2 or 'back' to return to sentience[/dim]",
                border_style="green",
            ))

            choice = pt_prompt("chats > ").strip().lower()
        else:
            choice = init_cmd
            init_cmd = None

        if choice in ("back", "exit", "quit", "q", "b", ":q", ""):
            break

        if choice in ("1", "show", "list"):
            _sub_show_loop(shell)
        elif choice in ("2", "delete", "remove"):
            _sub_delete_loop(shell)
        else:
            console.print(f"[dim]Unknown option '{choice}'. Choose 1, 2 or 'back'.[/dim]")
