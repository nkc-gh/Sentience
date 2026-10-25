"""
SENTIENCE Skills Command.

Manage custom AI skills:
- List: Table of custom skills with contiguous ID, name, description, created date.
- Add: Prompt skill name -> Open configured editor -> Save to Sentience skills folder.
- Remove: Remove by ID with automatic list renumbering.
Prompt stays in sub-modes until explicit 'back' instruction.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, List

from cli.prompts import prompt as pt_prompt
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

if TYPE_CHECKING:
    from cli.shell import SentienceShell

console = Console()


def slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[-\s]+", "_", slug) or "custom_skill"


def render_skills_table(skills: list) -> Table:
    table = Table(
        title="[bold green]Configured Sentience Skills[/bold green]",
        show_header=True,
        header_style="bold cyan",
        padding=(0, 1),
    )
    table.add_column("ID", justify="right", style="bold yellow", width=6)
    table.add_column("Skill Name", style="bold white", width=24)
    table.add_column("Description / Summary", style="dim white", width=36)
    table.add_column("Created", style="cyan", width=18)

    for idx, s in enumerate(skills, 1):
        desc = s.description or s.instructions[:40] or "[dim]Custom prompt[/dim]"
        if len(desc) > 35:
            desc = desc[:32] + "..."
        created = s.created_at[:16].replace("T", " ") if hasattr(s, "created_at") and s.created_at else ""
        table.add_row(
            str(idx),
            s.name,
            desc,
            created,
        )
    return table


def _open_skill(shell: SentienceShell, skill) -> None:
    """View skill contents and optionally edit in configured editor."""
    slug = slugify(skill.name)
    skills_dir = Path(shell.skills.skills_dir)
    skill_file = skills_dir / f"{slug}.md"
    content = ""
    if skill_file.exists():
        content = skill_file.read_text(encoding="utf-8")
    else:
        content = getattr(skill, "instructions", "") or "(No instructions recorded)"

    editor_cmd = shell.config.preferred_editor
    console.print()
    console.print(Panel(
        content,
        title=f"[bold green]Skill: {skill.name}[/bold green] [dim]({skill_file})[/dim]",
        border_style="green",
        subtitle=f"[dim]Type 'edit' or '{editor_cmd}' to open in {editor_cmd} · Press Enter to return to list[/dim]",
    ))
    console.print()

    action = pt_prompt(f"skill [view] (Type 'edit' to open in {editor_cmd} or Enter) > ").strip().lower()
    if action in ("edit", "e", "write", editor_cmd.lower()):
        from cli.editor import launch_editor
        if launch_editor(editor_cmd, skill_file):
            if skill_file.exists():
                updated_content = skill_file.read_text(encoding="utf-8")
                skill.instructions = updated_content
                shell.skills._save_skills()
            console.print(f"\n[bold green]✓ Successfully updated {skill.name}![/bold green]\n")
        else:
            console.print(f"[red]Error:[/red] Could not launch '{editor_cmd}'. Please edit {skill_file} manually.")


def _sub_list_loop(shell: SentienceShell) -> None:
    """Stay inside skills [list] until user inputs 'back', supporting 'open <id>'."""
    while True:
        skills = shell.skills.get_skills()
        if not skills:
            console.print("\n[dim yellow]No custom skills added yet. Use option 2 to add a skill.[/dim yellow]\n")
        else:
            console.print()
            console.print(render_skills_table(skills))
            console.print("[dim]Type 'open <id>' (e.g. 'open 1') to view a skill · 'back' to return[/dim]\n")

        cmd = pt_prompt("skills [list] > ").strip()
        cmd_lower = cmd.lower()
        if cmd_lower in ("back", "exit", "quit", "q", "b", ":q"):
            break
        if not cmd:
            continue

        parts = cmd.split(None, 1)
        sub_action = parts[0].lower()

        if sub_action in ("open", "view", "show", "cat"):
            if len(parts) > 1:
                target_arg = parts[1].strip()
            else:
                target_arg = pt_prompt("Enter Skill ID or name to open: ").strip()

            if target_arg.lower() in ("back", "cancel", "q", ""):
                continue

            target_skill = None
            try:
                idx = int(target_arg)
                if 1 <= idx <= len(skills):
                    target_skill = skills[idx - 1]
                else:
                    console.print(f"[red]Invalid Skill ID #{idx}. Valid range is 1 to {len(skills)}[/red]")
                    continue
            except ValueError:
                for s in skills:
                    if s.name.lower() == target_arg.lower() or s.id == target_arg:
                        target_skill = s
                        break
                if not target_skill:
                    console.print(f"[red]Skill '{target_arg}' not found.[/red]")
                    continue

            if target_skill:
                _open_skill(shell, target_skill)

        elif sub_action.isdigit():
            idx = int(sub_action)
            if 1 <= idx <= len(skills):
                _open_skill(shell, skills[idx - 1])
            else:
                console.print(f"[red]Invalid Skill ID #{idx}. Range is 1 to {len(skills)}[/red]")
        elif sub_action in ("r", "refresh"):
            continue
        else:
            console.print(f"[dim]Unknown command '{cmd}'. Use 'open <id>' or 'back'.[/dim]")


def _sub_add_loop(shell: SentienceShell) -> None:
    """Stay inside skills [add] until user inputs 'back'."""
    while True:
        console.print("\n[bold white]Add New Skill[/bold white]")
        console.print("[dim]Enter skill name or type 'back' to return to skills menu[/dim]")
        s_name = pt_prompt("skills [add] > ").strip()
        if s_name.lower() in ("back", "exit", "quit", "q", "b", ""):
            break

        slug = slugify(s_name)
        skills_dir = Path(shell.skills.skills_dir)
        skills_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skills_dir / f"{slug}.md"

        if not skill_file.exists():
            template = (
                f"# Skill: {s_name}\n\n"
                f"## Description\n"
                f"Describe what this skill does here...\n\n"
                f"## Instructions\n"
                f"Write step-by-step instructions or prompt guidance for Sentience here...\n"
            )
            skill_file.write_text(template, encoding="utf-8")

        editor_cmd = shell.config.preferred_editor
        console.print(f"[dim]Opening {editor_cmd} for: {skill_file}[/dim]")
        from cli.editor import launch_editor
        if not launch_editor(editor_cmd, skill_file):
            console.print(f"[red]Error:[/red] Could not launch '{editor_cmd}'. Please edit {skill_file} manually.")

        content = skill_file.read_text(encoding="utf-8") if skill_file.exists() else ""
        desc_line = ""
        for line in content.splitlines():
            line_s = line.strip()
            if line_s and not line_s.startswith("#"):
                desc_line = line_s
                break

        shell.skills.add_skill(
            name=s_name,
            description=desc_line or f"Skill file: {skill_file.name}",
            instructions=content,
        )
        console.print(f"\n[bold green]✓ Successfully added skill '{s_name}' saved to {skill_file}![/bold green]\n")


def _sub_remove_loop(shell: SentienceShell) -> None:
    """Stay inside skills [remove] until user inputs 'back'."""
    while True:
        skills = shell.skills.get_skills()
        if not skills:
            console.print("\n[dim yellow]No skills to remove.[/dim yellow]\n")
            cmd = pt_prompt("skills [remove] > ").strip().lower()
            if cmd in ("back", "exit", "quit", "q", "b", ""):
                break
            continue

        console.print()
        console.print(render_skills_table(skills))
        console.print()

        del_id_str = pt_prompt("skills [remove] (Enter ID or 'back') > ").strip()
        if del_id_str.lower() in ("back", "exit", "quit", "q", "b", ""):
            break

        try:
            del_id = int(del_id_str)
            if 1 <= del_id <= len(skills):
                target_skill = skills[del_id - 1]
                confirm = pt_prompt(f"Remove skill '{target_skill.name}'? (y/N): ").strip().lower()
                if confirm == "y":
                    shell.skills.remove_skill(target_skill.id)
                    console.print(f"[bold green]✓ Removed skill #{del_id}:[/bold green] {target_skill.name}")
                else:
                    console.print("[dim]Cancelled.[/dim]")
            else:
                console.print(f"[red]Invalid ID {del_id}. Range is 1 to {len(skills)}[/red]")
        except ValueError:
            console.print("[red]Please enter a valid numeric ID.[/red]")


def skills_command(shell: SentienceShell, args: List[str]) -> None:
    """Manage custom AI skills with persistent sub-mode loops."""
    init_cmd = args[0].lower() if args else None

    while True:
        editor_cmd = shell.config.preferred_editor
        if not init_cmd:
            console.print()
            console.print(Panel(
                "[bold white]Custom Skills Management[/bold white]\n"
                "[dim]Extend Sentience capabilities with custom prompts and instructions[/dim]\n\n"
                "  [bold yellow]1[/bold yellow]. [bold white]List skills[/bold white] (view custom skills table; 'open <id>' to view instructions)\n"
                f"  [bold yellow]2[/bold yellow]. [bold white]Add skill[/bold white] (enter name -> opens {editor_cmd} to write skill)\n"
                "  [bold yellow]3[/bold yellow]. [bold white]Remove skill[/bold white] (remove by ID with contiguous re-indexing)\n\n"
                "[dim]Type 1, 2, 3 or 'open <id>' or 'back' to return to sentience[/dim]",
                border_style="green",
            ))

            choice = pt_prompt("skills > ").strip().lower()
        else:
            choice = init_cmd
            init_cmd = None

        if choice in ("back", "exit", "quit", "q", "b", ":q", ""):
            break

        if choice in ("1", "list"):
            _sub_list_loop(shell)
        elif choice.startswith("open") or choice.startswith("view"):
            parts = choice.split(None, 1)
            target_arg = parts[1].strip() if len(parts) > 1 else ""
            skills = shell.skills.get_skills()
            if not target_arg and skills:
                console.print(render_skills_table(skills))
                target_arg = pt_prompt("Enter Skill ID to open: ").strip()
            if target_arg:
                target_skill = None
                try:
                    idx = int(target_arg)
                    if 1 <= idx <= len(skills):
                        target_skill = skills[idx - 1]
                except ValueError:
                    for s in skills:
                        if s.name.lower() == target_arg.lower() or s.id == target_arg:
                            target_skill = s
                            break
                if target_skill:
                    _open_skill(shell, target_skill)
                else:
                    console.print(f"[red]Skill '{target_arg}' not found.[/red]")
        elif choice in ("2", "add", "new"):
            _sub_add_loop(shell)
        elif choice in ("3", "remove", "delete"):
            _sub_remove_loop(shell)
        else:
            console.print(f"[dim]Unknown option '{choice}'. Choose 1, 2, 3 or 'back'.[/dim]")
