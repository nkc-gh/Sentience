"""Manage service connections used by Sentience."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cli.prompts import prompt as pt_prompt
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from connectors.registry import ConnectorManager

if TYPE_CHECKING:
    from cli.shell import SentienceShell

console = Console()

CATALOG = {
    "gmail": {
        "label": "Gmail",
        "provider": "Google OAuth",
        "steps": [
            "Create/select a Google Cloud project and enable Gmail API.",
            "Configure the OAuth consent screen and add your Google account as a test user.",
            "Create an OAuth Client ID with application type Desktop app.",
            "Download that client JSON. Sentience requests gmail.send, gmail.readonly, and account identity; it does not modify or mark emails read.",
        ],
        "url": "https://console.cloud.google.com/apis/credentials",
    },
    "calendar": {
        "label": "Calendar",
        "provider": "Google OAuth",
        "steps": [
            "Use the same Google Cloud project and OAuth Desktop client as Gmail.",
            "Enable Google Calendar API.",
            "Sentience requests calendar.events plus account identity; add your Google account as a test user.",
        ],
        "url": "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com",
    },
    "flights": {
        "label": "Flights",
        "provider": "Amadeus for Developers",
        "steps": [
            "Create an Amadeus for Developers app and copy its API Key and API Secret.",
            "Sandbox credentials are for development; production access and inventory vary by account.",
        ],
        "url": "https://developers.amadeus.com/",
    },
    "trains": {
        "label": "Trains",
        "provider": "RailCore",
        "steps": [
            "Create a RailCore developer account and copy the API key.",
            "This provider covers Indian railway data and is read-only; it does not book tickets.",
        ],
        "url": "https://railcore.tech/developers",
    },
}


def mask_key(key: str) -> str:
    return "••••••••••••" if key else "[dim]not connected[/dim]"


def render_connectors_table(connector_items: list[tuple[str, dict]]) -> Table:
    table = Table(title="[bold green]Connected Services[/bold green]", show_header=True, header_style="bold cyan")
    table.add_column("ID", justify="right", style="bold yellow", width=4)
    table.add_column("Service", style="bold white", min_width=26)
    table.add_column("Provider", style="cyan", min_width=22)
    table.add_column("Account / Credential", style="magenta", min_width=24)
    table.add_column("Status", justify="center", style="green", width=14)
    for index, (service_id, cfg) in enumerate(connector_items, 1):
        identity = cfg.get("account") or ("••••••••" if cfg.get("credential_ref") else "—")
        table.add_row(str(index), CATALOG.get(service_id, {}).get("label", service_id), cfg.get("provider", "—"), identity, cfg.get("status", "not tested"))
    return table


def _show_steps(service_id: str) -> None:
    item = CATALOG[service_id]
    console.print(Panel(
        f"[bold]{item['label']}[/bold]\nProvider: {item['provider']}\n\n"
        + "\n".join(f"{idx}. {step}" for idx, step in enumerate(item["steps"], 1))
        + f"\n\nSetup page: {item['url']}",
        title="Before connecting", border_style="cyan",
    ))


def _read_json_credentials(path_text: str) -> str:
    path = Path(path_text.strip().strip("\"' ")).expanduser()
    try:
        text = path.read_text(encoding="utf-8")
        json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read valid credentials JSON: {exc}") from exc
    return text


def _choose_google_credentials_path() -> str | None:
    console.print("\nSelect the downloaded OAuth client JSON:")
    console.print("  1. Choose a JSON file with the file picker\n  2. Enter the file path\n  3. Back")
    method = pt_prompt("credentials [1/2/3] > ").strip().casefold()
    if method in ("3", "back", "q", "quit", ""):
        return None
    if method in ("1", "picker"):
        from cli.commands.archive import launch_gui_picker

        selected = launch_gui_picker("json")
        if not selected:
            console.print("[dim]File picker cancelled.[/dim]")
            return None
        path = Path(selected).expanduser().resolve()
        console.print(f"[dim]Selected: {path}[/dim]")
        return str(path)
    if method in ("2", "path"):
        path = pt_prompt("Path to downloaded Desktop OAuth client JSON > ").strip()
        if not path or path.casefold() in ("back", "cancel"):
            return None
        return path
    console.print("[yellow]Choose 1, 2, or 3.[/yellow]")
    return _choose_google_credentials_path()


def _http_json(url: str, *, headers: dict[str, str] | None = None, data: dict[str, str] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(data).encode() if data else None,
        headers={"User-Agent": "Sentience/1.0", **({"Content-Type": "application/x-www-form-urlencoded"} if data else {}), **(headers or {})},
        method="POST" if data else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise RuntimeError(f"HTTP {exc.code}: {detail or exc.reason}") from exc


def _setup_service(manager: ConnectorManager, service_id: str) -> None:
    _show_steps(service_id)
    if service_id in ("gmail", "calendar"):
        from connectors import google_oauth

        current = manager.connectors
        client_text = None
        if not manager.get_secret(google_oauth.CLIENT_ACCOUNT):
            path = _choose_google_credentials_path()
            if path is None:
                return
            client_text = _read_json_credentials(path)
            google_oauth.save_client_credentials(client_text)
        scopes = list(google_oauth.OPENID_SCOPES)
        existing_token = manager.get_secret(google_oauth.TOKEN_ACCOUNT)
        if existing_token:
            try:
                scopes.extend(json.loads(existing_token).get("scopes", []))
            except (json.JSONDecodeError, AttributeError):
                pass
        if service_id == "gmail":
            scopes.extend([google_oauth.GMAIL_SEND, google_oauth.GMAIL_READONLY])
        else:
            scopes.append(google_oauth.CALENDAR_EVENTS)
        console.print("\nSentience will open your system browser for Google consent. OAuth tokens are saved in your OS credential vault.")
        if pt_prompt("Continue to Google sign-in? (y/N) > ").strip().casefold() not in ("y", "yes"):
            return
        account = google_oauth.authorize_google(scopes)
        manager.add_connector(service_id, {
            "provider": "Google OAuth", "status": "connected",
            "account": account.get("email", "Google account"),
            "credential_ref": "google-token",
            "scopes": scopes,
            "capabilities": ["send_email", "read_email"] if service_id == "gmail" else ["create_calendar_event"],
        })
        console.print(f"[green]Connected {service_id} for {account.get('email', 'your Google account')}.[/green]")
        return

    if service_id == "flights":
        client_id = pt_prompt("Amadeus API Key > ").strip()
        client_secret = pt_prompt("Amadeus API Secret (hidden) > ", is_password=True).strip()
        if not client_id or not client_secret:
            console.print("[yellow]Both Amadeus credentials are required.[/yellow]")
            return
        manager.save_secret("amadeus-client-id", client_id)
        manager.save_secret("amadeus-client-secret", client_secret)
        token = _http_json("https://test.api.amadeus.com/v1/security/oauth2/token", data={
            "grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret,
        })
        manager.save_secret("amadeus-access-token", token["access_token"])
        manager.add_connector(service_id, {"provider": "Amadeus", "status": "connected", "credential_ref": "amadeus-client-secret", "environment": "test", "capabilities": ["search_flights"]})
    elif service_id == "trains":
        api_key = pt_prompt("RailCore API Key (hidden; needs stations:read and journeys:read scopes) > ", is_password=True).strip()
        if not api_key:
            return
        manager.save_secret("railcore-api-key", api_key)
        _http_json("https://ir.railcore.tech/v1/stations/search?q=Delhi&limit=1", headers={"X-RailCore-Key": api_key})
        manager.add_connector(service_id, {"provider": "RailCore", "status": "connected", "credential_ref": "railcore-api-key", "coverage": "India", "capabilities": ["search_trains", "train_status"]})
    console.print(f"[green]Connected {CATALOG[service_id]['label']} and verified credentials.[/green]")


def _choose_service() -> str | None:
    table = Table(title="Add a service", show_header=True, header_style="bold cyan")
    table.add_column("#", style="yellow", width=3)
    table.add_column("Service", style="white")
    keys = ["gmail", "calendar", "flights", "trains"]
    for idx, key in enumerate(keys, 1):
        table.add_row(str(idx), CATALOG[key]["label"])
    console.print(table)
    choice = pt_prompt("Service number or 'back' > ").strip().casefold()
    if choice in ("back", "q", "quit", ""):
        return None
    try:
        return keys[int(choice) - 1]
    except (ValueError, IndexError):
        return choice if choice in CATALOG else None


def _remove_connector(manager: ConnectorManager) -> None:
    items = list(manager.connectors.items())
    if not items:
        console.print("[dim]No connected services.[/dim]")
        return
    console.print(render_connectors_table(items))
    choice = pt_prompt("Remove service ID or 'back' > ").strip().casefold()
    if choice in ("back", "q", ""):
        return
    try:
        service_id = items[int(choice) - 1][0]
    except (ValueError, IndexError):
        console.print("[red]That service ID is not valid.[/red]")
        return
    if pt_prompt(f"Disconnect {service_id}? (y/N) > ").strip().casefold() not in ("y", "yes"):
        return
    manager.remove_connector(service_id)
    if service_id == "gmail" and "calendar" not in manager.connectors:
        manager.remove_secret("google-token")
        manager.remove_secret("google-client")
    elif service_id == "calendar" and "gmail" not in manager.connectors:
        manager.remove_secret("google-token")
        manager.remove_secret("google-client")
    elif service_id == "flights":
        for secret in ("amadeus-client-id", "amadeus-client-secret", "amadeus-access-token"):
            manager.remove_secret(secret)
    elif service_id == "trains":
        manager.remove_secret("railcore-api-key")
    console.print(f"[green]Disconnected {service_id}.[/green]")


def connector_command(shell: SentienceShell, args: list[str]) -> None:
    manager = ConnectorManager(shell.config.config_dir)
    # Regional transit is intentionally out of the supported connector set.
    if manager.remove_connector("buses"):
        manager.remove_secret("navitia-api-key")
    while True:
        console.print(Panel(
            "[bold]Service connections[/bold]\n\n"
            "  1. List connected services\n  2. Add service\n  3. Edit / reconnect service\n  4. Remove service\n  5. Test service connection\n\n"
            "Type a number or 'back'.",
            border_style="green",
        ))
        choice = (args.pop(0) if args else pt_prompt("connector > ").strip()).casefold()
        if choice in ("back", "exit", "quit", "q", "b", ""):
            return
        if choice in ("1", "list"):
            items = list(manager.connectors.items())
            console.print(render_connectors_table(items) if items else "[dim]No services connected yet.[/dim]")
        elif choice in ("2", "add"):
            service_id = _choose_service()
            if service_id:
                try:
                    _setup_service(manager, service_id)
                except Exception as exc:
                    console.print(f"[bold red]Connection failed:[/bold red] {exc}")
        elif choice in ("3", "edit", "reconnect"):
            items = list(manager.connectors.items())
            if items:
                console.print(render_connectors_table(items))
                entry = pt_prompt("Service ID or 'back' > ").strip().casefold()
                service_id = entry if entry in manager.connectors else None
                if not service_id:
                    try:
                        service_id = items[int(entry) - 1][0]
                    except (ValueError, IndexError):
                        service_id = None
                if service_id:
                    try:
                        _setup_service(manager, service_id)
                    except Exception as exc:
                        console.print(f"[bold red]Update failed:[/bold red] {exc}")
            else:
                console.print("[dim]No connected services to edit.[/dim]")
        elif choice in ("4", "remove", "disconnect"):
            _remove_connector(manager)
        elif choice in ("5", "test"):
            items = list(manager.connectors.items())
            if not items:
                console.print("[dim]No connected services to test.[/dim]")
                continue
            console.print(render_connectors_table(items))
            entry = pt_prompt("Service ID or 'back' > ").strip().casefold()
            service_id = entry if entry in manager.connectors else None
            if not service_id:
                try:
                    service_id = items[int(entry) - 1][0]
                except (ValueError, IndexError):
                    service_id = None
            if service_id:
                try:
                    if service_id in ("gmail", "calendar"):
                        from connectors.google_oauth import test_calendar_connection, test_gmail_connection
                        if service_id == "gmail":
                            result = test_gmail_connection()
                            account = result.get("email", "Google account")
                            total = result.get("messages_total")
                            if total is not None:
                                account += f" · {total} messages"
                        else:
                            account = test_calendar_connection().get("summary", "Primary calendar")
                    elif service_id == "flights":
                        secret = manager.get_secret("amadeus-client-secret") or ""
                        client_id = manager.get_secret("amadeus-client-id") or ""
                        token = _http_json("https://test.api.amadeus.com/v1/security/oauth2/token", data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret})
                        manager.save_secret("amadeus-access-token", token["access_token"])
                        account = "Amadeus sandbox credentials valid"
                    elif service_id == "trains":
                        _http_json("https://ir.railcore.tech/v1/stations/search?q=Delhi&limit=1", headers={"X-RailCore-Key": manager.get_secret("railcore-api-key") or ""})
                        account = "RailCore API key valid"
                    else:
                        raise RuntimeError(f"Unsupported connector: {service_id}")
                    manager.connectors[service_id]["status"] = "connected"
                    manager.connectors[service_id]["account"] = manager.connectors[service_id].get("account") or account
                    manager._save()
                    console.print(f"[green]Connection test succeeded:[/green] {account}")
                except Exception as exc:
                    manager.connectors[service_id]["status"] = "error"
                    manager._save()
                    console.print(f"[red]Connection test failed:[/red] {exc}")
        else:
            console.print("[yellow]Choose 1–5 or 'back'.[/yellow]")
