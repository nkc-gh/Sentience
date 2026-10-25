"""
SENTIENCE Model Management Command.

Exactly 3 options:
1. Add provider:
   - Online API: Dynamic model discovery using provider name & API key with generous timeouts.
   - Local Runtime: The Localhost Port Roadmap (LM Studio, Ollama, Jan, llama.cpp, vLLM, Custom).
     Auto-detect format under the hood (no format intent prompt).
2. List providers: Grouped list (Provider Name -> its discovered models).
   Prompt stays at `model [list] >` until 'back'.
3. Remove provider: Table showing ID (1, 2, 3...), Name, masked API Key/URL.
   Remove by ID with automatic contiguous list re-indexing.
Prompt stays in sub-modes until explicit 'back' instruction.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlparse
from typing import TYPE_CHECKING, List, Optional

from cli.prompts import prompt as pt_prompt
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

if TYPE_CHECKING:
    from cli.shell import SentienceShell

console = Console()


# ═══════════════════════════════════════════════════════════
# The Localhost Port Roadmap
# ═══════════════════════════════════════════════════════════

LOCAL_ROADMAP = [
    {
        "name": "LM Studio",
        "url": "http://localhost:1234/v1",
        "port": 1234,
        "desc": "OpenAI-compatible server",
    },
    {
        "name": "Ollama",
        "url": "http://localhost:11434/v1",
        "port": 11434,
        "desc": "Native & OpenAI-compatible endpoints",
    },
    {
        "name": "Jan",
        "url": "http://localhost:1337/v1",
        "port": 1337,
        "desc": "Local AI OpenAI endpoint",
    },
    {
        "name": "llama.cpp (server)",
        "url": "http://localhost:8080/v1",
        "port": 8080,
        "desc": "Lightweight GGUF inference server",
    },
    {
        "name": "vLLM",
        "url": "http://localhost:8000/v1",
        "port": 8000,
        "desc": "High-throughput serving engine",
    },
]


class DiscoveredModel(str):
    """String subclass carrying model_id and display_name."""

    def __new__(cls, model_id: str, display_name: str = "", context_window: Optional[int] = None):
        obj = super().__new__(cls, model_id)
        obj.model_id = model_id
        obj.display_name = display_name or model_id
        obj.context_window = context_window
        return obj


_CONTEXT_FIELDS = {
    "context_window", "context_length", "context_limit", "max_context_length",
    "max_model_len", "max_sequence_length", "max_position_embeddings",
    "n_ctx", "input_token_limit", "inputtokenlimit", "max_input_tokens",
}


def _context_window_from_data(value) -> Optional[int]:
    """Read a provider-reported context field, never derive it from output limits."""
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).replace("-", "_").casefold()
            if normalized in _CONTEXT_FIELDS and isinstance(item, int) and not isinstance(item, bool) and item > 0:
                return item
        for item in value.values():
            if isinstance(item, (dict, list)):
                found = _context_window_from_data(item)
                if found:
                    return found
    elif isinstance(value, list):
        for item in value:
            found = _context_window_from_data(item)
            if found:
                return found
    return None


def mask_key(key: str) -> str:
    """Mask key completely so secrets never leak on screen or in tables."""
    if not key:
        return "[dim]none[/dim]"
    return "••••••••••••"


def render_roadmap_table() -> Table:
    """Render the standard Localhost Port Roadmap table."""
    table = Table(
        title="[bold green]The Localhost Port Roadmap[/bold green]",
        show_header=True,
        header_style="bold cyan",
        padding=(0, 1),
    )
    table.add_column("#", justify="right", style="bold yellow", width=4)
    table.add_column("Provider", style="bold white", width=22)
    table.add_column("Default API Endpoint", style="cyan", width=30)
    table.add_column("Port Number", justify="center", style="magenta", width=14)
    table.add_column("Description", style="dim", width=32)

    for idx, item in enumerate(LOCAL_ROADMAP, 1):
        table.add_row(
            str(idx),
            item["name"],
            item["url"],
            str(item["port"]),
            item["desc"],
        )
    table.add_row(
        str(len(LOCAL_ROADMAP) + 1),
        "Custom Endpoint",
        "http://localhost:<port>/v1",
        "Custom",
        "User-defined local port or address",
    )
    return table


def discover_models_for_provider(
    provider_name: str,
    api_key: str = "",
    base_url: str = "",
    local_format: str = "auto",
) -> List[DiscoveredModel]:
    """
    Search for models dynamically with generous timeouts (30-60s).
    Fetches real models from endpoints without fake hardcoded names.
    """
    p_lower = provider_name.lower().strip()
    discovered: List[DiscoveredModel] = []
    seen_ids: set[str] = set()

    def _add(m_id: str, display: str = "", context_window: Optional[int] = None):
        if not m_id or m_id in seen_ids:
            return
        seen_ids.add(m_id)
        discovered.append(DiscoveredModel(m_id, display_name=display or m_id, context_window=context_window))

    # 1. Local runtime discovery (Ollama / LM Studio / Jan / llama.cpp / vLLM / Custom)
    host = urlparse(base_url).hostname.casefold() if base_url and urlparse(base_url).hostname else ""
    is_local_host = host in ("localhost", "127.0.0.1", "::1") or host.startswith(("10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.", "172.2", "172.30.", "172.31."))
    is_local_endpoint = any(k in p_lower for k in ("ollama", "lm studio", "lmstudio", "jan", "llama.cpp", "llamacpp", "vllm", "local")) or is_local_host
    if is_local_endpoint:
        raw_base = (base_url or "http://localhost:11434").rstrip("/")
        # Derive base root (strip /v1 if present)
        root_base = raw_base[:-3] if raw_base.endswith("/v1") else raw_base

        # A. Try Ollama native endpoint (/api/tags)
        try:
            url = f"{root_base}/api/tags"
            req = urllib.request.Request(url, headers={"User-Agent": "Sentience/1.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
                for m in data.get("models", []):
                    name = m.get("name")
                    if name:
                        _add(name, display=name)
        except Exception:
            pass

        # B. Try OpenAI-compatible endpoint (/v1/models)
        if not discovered:
            for probe_url in (f"{root_base}/v1/models", f"{raw_base}/models", f"{root_base}/models"):
                try:
                    req = urllib.request.Request(probe_url, headers={"User-Agent": "Sentience/1.0"})
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        data = json.loads(resp.read().decode())
                        items = data.get("data") if isinstance(data.get("data"), list) else data.get("models", [])
                        if isinstance(items, list):
                            for m in items:
                                mid = m.get("id") or m.get("name")
                                if mid:
                                    _add(mid, display=mid, context_window=_context_window_from_data(m))
                        if discovered:
                            break
                except Exception:
                    pass

    # 2. Google / Gemini Generative Language API
    elif api_key and ("google" in p_lower or "gemini" in p_lower):
        # Native Google Generative Language endpoint
        native_url = (base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        if not native_url.endswith("/models"):
            native_url = f"{native_url}/models?key={api_key}"
        elif "?" not in native_url:
            native_url = f"{native_url}?key={api_key}"
        try:
            req = urllib.request.Request(native_url, headers={"User-Agent": "Sentience/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
                for m in data.get("models", []):
                    raw_name = m.get("name", "")
                    clean_id = raw_name.replace("models/", "") if raw_name else ""
                    if clean_id:
                        display = m.get("displayName") or clean_id
                        _add(clean_id, display=display, context_window=_context_window_from_data(m))
        except Exception:
            pass

        # Try Google OpenAI-compatible endpoint as secondary discovery
        if not discovered:
            try:
                ep_openai = "https://generativelanguage.googleapis.com/v1beta/openai/models"
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "User-Agent": "Sentience/1.0",
                }
                req = urllib.request.Request(ep_openai, headers=headers)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode())
                    for m in data.get("data", []):
                        raw_name = m.get("id", "")
                        clean_id = raw_name.replace("models/", "") if raw_name else ""
                        if clean_id:
                            _add(clean_id, display=clean_id, context_window=_context_window_from_data(m))
            except Exception:
                pass

    # 3. Online cloud providers (OpenAI / Anthropic / Groq / DeepSeek / Mistral / Together / OpenRouter / etc.)
    elif api_key or base_url:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Sentience/1.0",
        }

        if base_url:
            b_clean = base_url.rstrip("/")
            endpoint = b_clean if b_clean.endswith("/models") else f"{b_clean}/models"
        elif "groq" in p_lower:
            endpoint = "https://api.groq.com/openai/v1/models"
        elif "deepseek" in p_lower:
            endpoint = "https://api.deepseek.com/v1/models"
        elif "mistral" in p_lower:
            endpoint = "https://api.mistral.ai/v1/models"
        elif "together" in p_lower:
            endpoint = "https://api.together.xyz/v1/models"
        elif "openrouter" in p_lower:
            endpoint = "https://openrouter.ai/api/v1/models"
        elif "anthropic" in p_lower or "claude" in p_lower:
            endpoint = "https://api.anthropic.com/v1/models"
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
            headers.pop("Authorization", None)
        elif "xai" in p_lower or "grok" in p_lower:
            endpoint = "https://api.x.ai/v1/models"
        elif "cohere" in p_lower:
            endpoint = "https://api.cohere.com/v1/models"
        elif "perplexity" in p_lower:
            endpoint = "https://api.perplexity.ai/models"
        else:
            endpoint = "https://api.openai.com/v1/models"

        try:
            req = urllib.request.Request(endpoint, headers=headers)
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode())
                items = data.get("data") if isinstance(data.get("data"), list) else (
                    data.get("models") if isinstance(data.get("models"), list) else []
                )
                if isinstance(data, list):
                    items = data

                for m in items:
                    if isinstance(m, dict):
                        mid = m.get("id") or m.get("name")
                        disp = m.get("display_name") or m.get("name") or mid
                        if mid:
                            _add(mid, display=disp, context_window=_context_window_from_data(m))
                    elif isinstance(m, str):
                        _add(m, display=m)
        except Exception:
            pass

    return discovered


def render_remove_provider_table(providers: list) -> Table:
    table = Table(
        title="[bold green]Configured AI Providers[/bold green]",
        show_header=True,
        header_style="bold cyan",
        padding=(0, 1),
    )
    table.add_column("ID", justify="right", style="bold yellow", width=6)
    table.add_column("Provider Name", style="bold white", width=22)
    table.add_column("API Key / URL", style="cyan", width=26)
    table.add_column("Type", style="magenta", width=10)
    table.add_column("Models Loaded", justify="center", style="green", width=16)

    for idx, p in enumerate(providers, 1):
        key_snippet = mask_key(p.api_key) if p.api_key else (p.base_url[:22] + "..." if p.base_url else "[dim]none[/dim]")
        m_count = len(getattr(p, "models", []))
        table.add_row(
            str(idx),
            p.name,
            key_snippet,
            p.kind,
            str(m_count),
        )
    return table


def _sub_add_loop(shell: SentienceShell) -> None:
    """Stay inside model [add] until user inputs 'back'."""
    while True:
        console.print("\n[bold white]Add AI Provider[/bold white]")
        console.print("  [bold yellow]1[/bold yellow]. [cyan]Online API[/cyan]      [dim](Google, OpenAI, Anthropic, Groq, DeepSeek, Mistral, etc.)[/dim]")
        console.print("  [bold yellow]2[/bold yellow]. [green]Local Runtime[/green]   [dim](The Localhost Port Roadmap: LM Studio, Ollama, Jan, etc.)[/dim]")
        console.print("[dim]Enter 1, 2 or type 'back' to return to model menu[/dim]")

        ptype_in = pt_prompt("model [add type] > ").strip().lower()
        if ptype_in in ("back", "exit", "quit", "q", "b", ""):
            break

        is_local = ptype_in in ("2", "local", "l")

        if is_local:
            # Display The Localhost Port Roadmap
            console.print()
            console.print(render_roadmap_table())
            console.print()

            rm_choice = pt_prompt("Select local runtime [1-6] (or 'back') > ").strip().lower()
            if rm_choice in ("back", "exit", "quit", "q", "b", ""):
                continue

            try:
                c_idx = int(rm_choice)
            except ValueError:
                c_idx = -1

            if 1 <= c_idx <= len(LOCAL_ROADMAP):
                item = LOCAL_ROADMAP[c_idx - 1]
                p_name = item["name"]
                base_url = item["url"]
                api_key = ""
            elif c_idx == len(LOCAL_ROADMAP) + 1:
                # Custom local endpoint
                cust_name = pt_prompt("Custom provider name (e.g. LocalAI): ").strip()
                p_name = cust_name if cust_name else "Custom Local"
                ep_in = pt_prompt("Endpoint or Port (e.g. 8080 or http://localhost:8080/v1): ").strip()
                if not ep_in:
                    ep_in = "8080"
                if ep_in.isdigit():
                    base_url = f"http://localhost:{ep_in}/v1"
                elif not ep_in.startswith("http"):
                    base_url = f"http://{ep_in}"
                else:
                    base_url = ep_in
                api_key = ""
            else:
                console.print("[red]Invalid selection. Please choose 1 to 6.[/red]")
                continue

            console.print(f"\n[cyan]Auto-detecting and loading models from {p_name} ({base_url})...[/cyan]")
            discovered_models = discover_models_for_provider(p_name, api_key="", base_url=base_url)

            if not discovered_models:
                console.print(f"\n[yellow]No running models found at {base_url}.[/yellow]")
                console.print(f"[dim]Make sure {p_name} is running and has models loaded.[/dim]")
                man_name = pt_prompt(f"Enter model name manually for {p_name} (or press Enter to skip): ").strip()
                if man_name:
                    discovered_models = [DiscoveredModel(man_name, display_name=man_name)]

        else:
            # Online API
            console.print("\n[dim]Enter online provider name (e.g. Google, OpenAI, Anthropic, Groq, DeepSeek, Mistral, Together, OpenRouter):[/dim]")
            p_name = pt_prompt("provider name > ").strip()
            if p_name.lower() in ("back", "exit", "quit", "q", "b", ""):
                continue

            api_key = pt_prompt(f"Enter API Key for {p_name} (hidden input): ", is_password=True).strip()
            if not api_key:
                console.print("[yellow]API key is empty. Online fetching requires an API key.[/yellow]")
                continue

            custom_url = pt_prompt("Custom Base URL (optional, press Enter to use default API endpoint): ").strip()
            base_url = custom_url

            console.print(f"\n[cyan]Fetching available models for {p_name} using API key... (please wait)[/cyan]")
            discovered_models = discover_models_for_provider(p_name, api_key=api_key, base_url=base_url)

            if not discovered_models:
                console.print(f"\n[yellow]Could not automatically discover models from {p_name}.[/yellow]")
                console.print("[dim]Please verify your API key or network connection.[/dim]")
                man_name = pt_prompt(f"Enter model name manually for {p_name} (or press Enter to skip): ").strip()
                if man_name:
                    discovered_models = [DiscoveredModel(man_name, display_name=man_name)]

        # Replace existing provider with same name if any
        for p in shell.providers.get_providers():
            if p.name.lower() == p_name.lower():
                shell.providers.remove_provider(p.id)

        kind = "local" if is_local else "cloud"
        new_provider = shell.providers.add_provider(p_name, kind, api_key=api_key, base_url=base_url)

        added_count = 0
        for dm in discovered_models:
            mid = getattr(dm, "model_id", str(dm))
            disp = getattr(dm, "display_name", mid)
            shell.providers.add_model(
                new_provider.id,
                disp,
                mid,
                context_window=getattr(dm, "context_window", None),
            )
            added_count += 1

        scope_label = "[green]Local[/green]" if is_local else "[cyan]Online Cloud[/cyan]"
        console.print(f"[bold green]✓ Successfully registered {scope_label} provider '{p_name}' with {added_count} models discovered![/bold green]\n")


def _sub_list_loop(shell: SentienceShell) -> None:
    """Stay inside model [list] until user inputs 'back'."""
    while True:
        providers = list(shell.providers.get_providers())
        if not providers:
            console.print("\n[dim yellow]No providers in database. Use option 1 to add a provider.[/dim yellow]\n")
        else:
            console.print("\n[bold green]Providers & Discovered Models[/bold green]")
            for idx, p in enumerate(providers, 1):
                key_preview = mask_key(p.api_key) if p.api_key else (p.base_url or "local")
                console.print(f"\n[bold yellow]Provider {idx}:[/bold yellow] [bold white]{p.name}[/bold white] [dim]({p.kind} | Key/URL: {key_preview})[/dim]")
                if p.models:
                    console.print("  [cyan]Available Models:[/cyan]")
                    for m in p.models:
                        limit = f" · context {m.context_window:,}" if getattr(m, "context_window", None) else ""
                        console.print(f"    • [white]{m.model_id}[/white] [dim]({m.name}{limit})[/dim]")
                else:
                    console.print("  [dim]No models loaded.[/dim]")
            console.print()

        cmd = pt_prompt("model [list] > ").strip().lower()
        if cmd in ("back", "exit", "quit", "q", "b", ":q"):
            break


def _sub_remove_loop(shell: SentienceShell) -> None:
    """Stay inside model [remove] until user inputs 'back'."""
    while True:
        providers = list(shell.providers.get_providers())
        if not providers:
            console.print("\n[dim yellow]No providers to remove.[/dim yellow]\n")
            cmd = pt_prompt("model [remove] > ").strip().lower()
            if cmd in ("back", "exit", "quit", "q", "b", ""):
                break
            continue

        console.print()
        console.print(render_remove_provider_table(providers))
        console.print()

        del_id_str = pt_prompt("model [remove] (Enter ID or 'back') > ").strip()
        if del_id_str.lower() in ("back", "exit", "quit", "q", "b", ""):
            break

        try:
            del_id = int(del_id_str)
            if 1 <= del_id <= len(providers):
                target_p = providers[del_id - 1]
                confirm = pt_prompt(f"Remove provider '{target_p.name}' and all its models? (y/N): ").strip().lower()
                if confirm == "y":
                    shell.providers.remove_provider(target_p.id)
                    console.print(f"[bold green]✓ Successfully removed provider #{del_id}:[/bold green] {target_p.name}")
                else:
                    console.print("[dim]Cancelled.[/dim]")
            else:
                console.print(f"[red]Invalid ID {del_id}. Range is 1 to {len(providers)}[/red]")
        except ValueError:
            console.print("[red]Please enter a valid numeric ID.[/red]")


def model_command(shell: SentienceShell, args: List[str]) -> None:
    """Model management, discovery, and context-window configuration."""
    init_cmd = args[0].lower() if args else None

    while True:
        if not init_cmd:
            console.print()
            console.print(Panel(
                "[bold white]Model & Provider Management[/bold white]\n"
                "[dim]Register AI providers and discover supported models[/dim]\n\n"
                "  [bold yellow]1[/bold yellow]. [bold white]Add provider[/bold white] (Online API or Localhost Port Roadmap)\n"
                "  [bold yellow]2[/bold yellow]. [bold white]List providers[/bold white] (view provider by provider with discovered models)\n"
                "  [bold yellow]3[/bold yellow]. [bold white]Remove provider[/bold white] (remove by ID with contiguous re-indexing)\n\n"
                "[dim]Type 1, 2, 3 or 'back' to return to sentience[/dim]",
                border_style="green",
            ))

            choice = pt_prompt("model > ").strip().lower()
        else:
            choice = init_cmd
            init_cmd = None

        if choice in ("back", "exit", "quit", "q", "b", ":q", ""):
            break

        if choice in ("1", "add", "add provider"):
            _sub_add_loop(shell)
        elif choice in ("2", "list", "list providers", "show"):
            _sub_list_loop(shell)
        elif choice in ("3", "remove", "delete"):
            _sub_remove_loop(shell)
        else:
            console.print(f"[dim]Unknown option '{choice}'. Choose 1, 2, 3 or 'back'.[/dim]")

