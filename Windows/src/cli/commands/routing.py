"""
SENTIENCE Dynamic Model Routing Command.

Provides full management of model routing policies for the 4 core task modes:
1. chat       — Conversational assistant and general dialogue
2. reasoning  — Deep logic, complex planning, and multi-step deduction
3. code       — Code generation, debugging, refactoring, and AST inspection
4. embedding  — Vector embeddings, semantic indexing, and RAG retrieval

Each task mode can be independently configured with:
- Dedicated Local model (strictly private, local runtimes)
- Dedicated Online model (cloud AI providers)
- Scope policy (Manual Choice, Strict Local, Online Only), without automatic failover
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from cli.prompts import prompt as pt_prompt
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from routing.registry import TASK_MODES, TASK_MODE_INFO, LLMModel, TaskRoute

if TYPE_CHECKING:
    from cli.shell import SentienceShell

console = Console()


def render_routes_table(shell: SentienceShell) -> Table:
    """Render a comprehensive table showing all 4 task routes and settings."""
    table = Table(
        title="[bold green]AI Model Routing Settings[/bold green]",
        show_header=True,
        header_style="bold cyan",
        padding=(0, 1),
    )
    table.add_column("#", justify="right", style="bold yellow", width=4)
    table.add_column("Task Mode", style="bold white", width=18)
    table.add_column("Local candidates · priority order", style="green", width=34)
    table.add_column("Online candidates · priority order", style="cyan", width=34)
    table.add_column("Policy Setting", justify="center", width=20)

    routes = shell.providers.get_routes()

    for idx, mode in enumerate(TASK_MODES, 1):
        info = TASK_MODE_INFO.get(mode, {"name": mode.capitalize(), "icon": "◈"})
        mode_label = f"{info.get('icon', '◈')} {info.get('name', mode.capitalize())}"
        route: Optional[TaskRoute] = routes.get(mode)

        # Local model display
        local_name = ", ".join(
            (shell.providers.get_model_by_id(mid).name if shell.providers.get_model_by_id(mid) else mid)
            for mid in (route.local_model_ids if route else [])
        ) or "[dim]none[/dim]"

        # Online model display
        online_name = ", ".join(
            (shell.providers.get_model_by_id(mid).name if shell.providers.get_model_by_id(mid) else mid)
            for mid in (route.online_model_ids if route else [])
        ) or "[dim]none[/dim]"

        # Policy display
        scope = route.active_scope if route else "hybrid"
        if scope == "local":
            policy_badge = "[bold yellow]Strict Local[/bold yellow]"
        elif scope == "online":
            policy_badge = "[bold cyan]Online Only[/bold cyan]"
        else:
            policy_badge = "[bold green]Manual Choice[/bold green]"

        table.add_row(
            str(idx),
            mode_label,
            local_name,
            online_name,
            policy_badge,
        )

    return table


def _sub_list_routes(shell: SentienceShell) -> None:
    """Display the routing table in a persistent loop until 'back'."""
    while True:
        console.print()
        console.print(render_routes_table(shell))
        console.print()

        cmd = pt_prompt("routing [list] > ").strip().lower()
        if cmd in ("back", "exit", "quit", "q", "b", ":q"):
            break


def _sub_configure_route(shell: SentienceShell) -> None:
    """Configure or add a model assignment for one of the 4 task modes."""
    while True:
        console.print("\n[bold white]Configure Task Route (Add / Set Model)[/bold white]")
        console.print("[dim]Select task mode to configure:[/dim]")
        for idx, mode in enumerate(TASK_MODES, 1):
            info = TASK_MODE_INFO.get(mode, {})
            console.print(f"  [bold yellow]{idx}[/bold yellow]. {info.get('icon', '◈')} [bold white]{info.get('name', mode)}[/bold white] [dim]— {info.get('desc', '')}[/dim]")
        console.print("[dim]Enter 1-4, or type 'back' to return to routing menu[/dim]")

        t_in = pt_prompt("routing [task #] > ").strip().lower()
        if t_in in ("back", "exit", "quit", "q", "b", ""):
            break

        mode_key: Optional[str] = None
        try:
            t_idx = int(t_in)
            if 1 <= t_idx <= len(TASK_MODES):
                mode_key = TASK_MODES[t_idx - 1]
        except ValueError:
            for m in TASK_MODES:
                if m.lower() == t_in or TASK_MODE_INFO[m]["name"].lower() == t_in:
                    mode_key = m
                    break

        if not mode_key:
            console.print("[red]Invalid task selection. Please choose 1, 2, 3, or 4.[/red]")
            continue

        info = TASK_MODE_INFO.get(mode_key, {})
        console.print(f"\n[bold cyan]Configuring route for:[/bold cyan] {info.get('icon', '◈')} [bold white]{info.get('name', mode_key)}[/bold white]")
        console.print("  [bold yellow]1[/bold yellow]. [green]Local Models[/green]   [dim](Choose one or more, in preference order)[/dim]")
        console.print("  [bold yellow]2[/bold yellow]. [cyan]Online Models[/cyan]  [dim](Choose one or more, in preference order)[/dim]")
        console.print("[dim]Enter 1 or 2, or 'back' to cancel[/dim]")

        slot_in = pt_prompt("slot [1/2] > ").strip().lower()
        if slot_in in ("back", "q", "b", ""):
            continue

        is_local_slot = slot_in in ("1", "local", "l")
        target_slot_name = "Local" if is_local_slot else "Online"

        # Fetch models for the chosen slot
        if is_local_slot:
            available_models = shell.providers.get_local_models()
        else:
            available_models = shell.providers.get_online_models()

        if not available_models:
            console.print(f"\n[yellow]No registered {target_slot_name} models found. Add a provider and discover its models with 'model' first.[/yellow]")
            continue

        # If models exist, display numbered selection table
        m_table = Table(
            title=f"[bold cyan]Available {target_slot_name} Models[/bold cyan]",
            show_header=True,
            header_style="bold green",
            padding=(0, 1),
        )
        m_table.add_column("#", justify="right", style="bold yellow", width=4)
        m_table.add_column("Model Name", style="bold white", width=26)
        m_table.add_column("Provider", style="cyan", width=18)
        m_table.add_column("Model ID", style="dim", width=28)

        for m_idx, m in enumerate(available_models, 1):
            m_table.add_row(str(m_idx), m.name, m.provider, m.model_id)

        console.print()
        console.print(m_table)
        console.print()

        m_sel = pt_prompt(f"Select model numbers in preference order, comma-separated (1-{len(available_models)}), or 'back': ").strip()
        if m_sel.lower() in ("back", "q", "b", ""):
            continue

        selected_ids = []
        try:
            for item in m_sel.split(","):
                number = int(item.strip())
                if not 1 <= number <= len(available_models):
                    raise ValueError
                model_id = available_models[number - 1].id
                if model_id not in selected_ids:
                    selected_ids.append(model_id)
        except ValueError:
            console.print("[red]Enter valid model numbers separated by commas.[/red]")
            continue

        if is_local_slot:
            shell.providers.set_route_models(mode_key, local_model_ids=selected_ids)
        else:
            shell.providers.set_route_models(mode_key, online_model_ids=selected_ids)

        console.print(f"\n[bold green]✓ Set {len(selected_ids)} {target_slot_name.lower()} candidate(s) for '{mode_key}' in preference order.[/bold green]\n")


def _sub_edit_policy(shell: SentienceShell) -> None:
    """Edit the active dispatch policy (Hybrid vs Strict Local vs Online Only) for a task mode."""
    while True:
        console.print("\n[bold white]Edit Task Routing Policy[/bold white]")
        console.print("[dim]Select task mode to modify policy:[/dim]")
        for idx, mode in enumerate(TASK_MODES, 1):
            route = shell.providers.get_route(mode)
            curr_policy = route.active_scope if route else "hybrid"
            info = TASK_MODE_INFO.get(mode, {})
            console.print(f"  [bold yellow]{idx}[/bold yellow]. {info.get('icon', '◈')} [bold white]{info.get('name', mode)}[/bold white] [dim](Current: {curr_policy})[/dim]")
        console.print("[dim]Enter 1-4, or type 'back' to return to routing menu[/dim]")

        t_in = pt_prompt("routing [policy #] > ").strip().lower()
        if t_in in ("back", "exit", "quit", "q", "b", ""):
            break

        mode_key: Optional[str] = None
        try:
            t_idx = int(t_in)
            if 1 <= t_idx <= len(TASK_MODES):
                mode_key = TASK_MODES[t_idx - 1]
        except ValueError:
            for m in TASK_MODES:
                if m.lower() == t_in:
                    mode_key = m
                    break

        if not mode_key:
            console.print("[red]Invalid task selection.[/red]")
            continue

        console.print(f"\n[bold cyan]Select Policy for '{mode_key}':[/bold cyan]")
        console.print("  [bold yellow]1[/bold yellow]. [bold green]Manual Choice[/bold green] [dim]— Allows both explicitly assigned online/local candidates[/dim]")
        console.print("  [bold yellow]2[/bold yellow]. [bold yellow]Strict Local[/bold yellow] [dim]— Uses only a local model; never falls back online[/dim]")
        console.print("  [bold yellow]3[/bold yellow]. [bold cyan]Online Only[/bold cyan] [dim]— Allows only online candidates; no local fallback[/dim]")
        console.print("[dim]Enter 1, 2, 3 or 'back' to cancel[/dim]")

        p_sel = pt_prompt("policy [1/2/3] > ").strip().lower()
        if p_sel in ("back", "q", "b", ""):
            continue

        if p_sel in ("2", "local", "strict", "l"):
            new_policy = "local"
        elif p_sel in ("3", "online", "online-only", "cloud", "o"):
            new_policy = "online"
        elif p_sel in ("1", "hybrid", "h"):
            new_policy = "hybrid"
        else:
            console.print("[red]Choose 1, 2, or 3.[/red]")
            continue

        shell.providers.set_route(mode_key, active_scope=new_policy)
        console.print(f"\n[bold green]✓ Updated policy for '{mode_key}' to:[/bold green] [white]{new_policy}[/white]\n")


def _sub_remove_route(shell: SentienceShell) -> None:
    """Clear model mappings or reset task routes to defaults."""
    while True:
        console.print("\n[bold white]Remove / Clear Route Mapping[/bold white]")
        console.print("[dim]Select task mode to clear:[/dim]")
        for idx, mode in enumerate(TASK_MODES, 1):
            route = shell.providers.get_route(mode)
            l_str = ", ".join(route.local_model_ids) if route and route.local_model_ids else "none"
            o_str = ", ".join(route.online_model_ids) if route and route.online_model_ids else "none"
            info = TASK_MODE_INFO.get(mode, {})
            console.print(f"  [bold yellow]{idx}[/bold yellow]. {info.get('icon', '◈')} [bold white]{info.get('name', mode)}[/bold white] [dim](Local: {l_str} | Online: {o_str})[/dim]")
        console.print("[dim]Enter 1-4, or type 'back' to return to routing menu[/dim]")

        t_in = pt_prompt("routing [remove #] > ").strip().lower()
        if t_in in ("back", "exit", "quit", "q", "b", ""):
            break

        mode_key: Optional[str] = None
        try:
            t_idx = int(t_in)
            if 1 <= t_idx <= len(TASK_MODES):
                mode_key = TASK_MODES[t_idx - 1]
        except ValueError:
            for m in TASK_MODES:
                if m.lower() == t_in:
                    mode_key = m
                    break

        if not mode_key:
            console.print("[red]Invalid task selection.[/red]")
            continue

        console.print(f"\n[bold cyan]What would you like to clear for '{mode_key}'?[/bold cyan]")
        console.print("  [bold yellow]1[/bold yellow]. [white]Clear Local Model assignment[/white]")
        console.print("  [bold yellow]2[/bold yellow]. [white]Clear Online Model assignment[/white]")
        console.print("  [bold yellow]3[/bold yellow]. [white]Reset entire route (clear both and reset to hybrid)[/white]")
        console.print("[dim]Enter 1, 2, 3 or 'back' to cancel[/dim]")

        c_sel = pt_prompt("action [1/2/3] > ").strip().lower()
        if c_sel in ("back", "q", "b", ""):
            continue

        if c_sel == "1":
            shell.providers.remove_route(mode_key, target="local")
            console.print(f"\n[bold green]✓ Cleared local model assignment for '{mode_key}'.[/bold green]\n")
        elif c_sel == "2":
            shell.providers.remove_route(mode_key, target="online")
            console.print(f"\n[bold green]✓ Cleared online model assignment for '{mode_key}'.[/bold green]\n")
        elif c_sel in ("3", "all", "reset"):
            shell.providers.remove_route(mode_key, target="all")
            console.print(f"\n[bold green]✓ Reset route for '{mode_key}' to defaults.[/bold green]\n")


def routing_command(shell: SentienceShell, args: List[str]) -> None:
    """Interactive Model Routing management command."""
    init_cmd = args[0].lower() if args else None

    while True:
        if not init_cmd:
            console.print()
            console.print(Panel(
                "[bold white]Dynamic Model Routing & Task Policies[/bold white]\n"
                "[dim]Configure ordered model candidates for each task. Manual Choice allows both scopes; Local/Online restrict candidates without failover.[/dim]\n\n"
                "  [bold yellow]1[/bold yellow]. [bold white]List task routes[/bold white]     [dim](View configured models & active effective targets)[/dim]\n"
                "  [bold yellow]2[/bold yellow]. [bold white]Configure route[/bold white]      [dim](Assign Local or Online model for a task mode)[/dim]\n"
                "  [bold yellow]3[/bold yellow]. [bold white]Edit route policy[/bold white]    [dim](Switch between Manual Choice, Strict Local, Online Only)[/dim]\n"
                "  [bold yellow]4[/bold yellow]. [bold white]Remove / Reset route[/bold white] [dim](Clear model mappings or restore defaults)[/dim]\n\n"
                "[dim]Type 1, 2, 3, 4 or 'back' to return to sentience[/dim]",
                border_style="green",
                title="[bold green]◈ routing[/bold green]",
            ))

            choice = pt_prompt("routing > ").strip().lower()
        else:
            choice = init_cmd
            init_cmd = None

        if choice in ("back", "exit", "quit", "q", "b", ":q", ""):
            break

        if choice in ("1", "list", "show", "routes"):
            _sub_list_routes(shell)
        elif choice in ("2", "add", "set", "configure"):
            _sub_configure_route(shell)
        elif choice in ("3", "edit", "policy", "mode"):
            _sub_edit_policy(shell)
        elif choice in ("4", "remove", "delete", "clear", "reset"):
            _sub_remove_route(shell)
        else:
            console.print(f"[dim]Unknown option '{choice}'. Choose 1, 2, 3, 4 or 'back'.[/dim]")
