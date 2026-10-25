"""Minimal Google installed-app OAuth flow for Sentience connectors."""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from typing import Any

SERVICE = "sentience-connectors"
CLIENT_ACCOUNT = "google-client"
TOKEN_ACCOUNT = "google-token"
GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"
GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
CALENDAR_EVENTS = "https://www.googleapis.com/auth/calendar.events"
OPENID_SCOPES = ("openid", "email")


def _keyring():
    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError("Secure credential storage is unavailable. Install Sentience's keyring dependency and unlock your OS keyring.") from exc
    backend = keyring.get_keyring()
    if getattr(backend, "priority", 0) <= 0:
        raise RuntimeError("No secure OS keyring backend is available. Set up Secret Service (Linux) before connecting accounts.")
    return keyring


def save_client_credentials(raw_json: str) -> dict[str, Any]:
    try:
        data = json.loads(raw_json)
        client = data.get("installed") or data.get("web")
        if not isinstance(client, dict) or not client.get("client_id") or not client.get("client_secret"):
            raise ValueError
    except (json.JSONDecodeError, ValueError, AttributeError) as exc:
        raise ValueError("Choose a valid Google OAuth client credentials JSON file.") from exc
    _keyring().set_password(SERVICE, CLIENT_ACCOUNT, json.dumps(client))
    return client


def _post_form(url: str, values: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(values).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Sentience/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except Exception as exc:
        detail = ""
        if hasattr(exc, "read"):
            detail = exc.read().decode(errors="replace")[:400]
        raise RuntimeError(f"Google OAuth request failed: {detail or exc}") from exc


def authorize_google(required_scopes: list[str]) -> dict[str, Any]:
    """Run local-loopback OAuth with PKCE, refresh token and minimal scopes."""
    keyring = _keyring()
    raw_client = keyring.get_password(SERVICE, CLIENT_ACCOUNT)
    if not raw_client:
        raise RuntimeError("Add the Google OAuth Desktop credentials JSON first.")
    client = json.loads(raw_client)
    scopes = list(dict.fromkeys([*OPENID_SCOPES, *required_scopes]))
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    result: dict[str, str] = {}
    received = threading.Event()

    class Callback(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result.update({key: values[0] for key, values in query.items() if values})
            self.send_response(200 if result.get("state") == state and result.get("code") else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h2>Sentience connected</h2><p>You can close this tab and return to Sentience.</p>")
            received.set()

        def log_message(self, fmt, *args):
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Callback)
    server.timeout = 180
    redirect_uri = f"http://127.0.0.1:{server.server_port}/oauth2callback"
    auth_params = {
        "client_id": client["client_id"], "redirect_uri": redirect_uri,
        "response_type": "code", "scope": " ".join(scopes), "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
        "access_type": "offline", "prompt": "consent",
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(auth_params)
    opened = webbrowser.open(auth_url)
    if not opened:
        print(f"Open this URL to authorize Sentience:\n{auth_url}")
    try:
        deadline = time.monotonic() + 180
        while not received.is_set() and time.monotonic() < deadline:
            server.handle_request()
        if not received.is_set():
            raise TimeoutError("Google sign-in timed out after 3 minutes.")
    finally:
        server.server_close()
    if result.get("state") != state:
        raise RuntimeError("Google OAuth state validation failed.")
    if result.get("error"):
        raise RuntimeError(f"Google authorization failed: {result['error']}")
    if not result.get("code"):
        raise RuntimeError("Google did not return an authorization code.")
    tokens = _post_form(client.get("token_uri", "https://oauth2.googleapis.com/token"), {
        "client_id": client["client_id"], "client_secret": client["client_secret"],
        "code": result["code"], "code_verifier": verifier,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    })
    tokens["scopes"] = str(tokens.get("scope", " ".join(scopes))).split()
    keyring.set_password(SERVICE, TOKEN_ACCOUNT, json.dumps(tokens))
    return google_user_info(tokens["access_token"])


def _get_token() -> dict[str, Any]:
    keyring = _keyring()
    raw = keyring.get_password(SERVICE, TOKEN_ACCOUNT)
    if not raw:
        raise RuntimeError("Google is not connected. Use connector > Add > Gmail or Calendar.")
    token = json.loads(raw)
    if "refresh_token" not in token:
        raise RuntimeError("Google refresh token is missing. Reconnect Google from connector > Edit.")
    client = json.loads(keyring.get_password(SERVICE, CLIENT_ACCOUNT) or "{}")
    if not client:
        raise RuntimeError("Google OAuth client credentials are missing; reconnect Google.")
    refreshed = _post_form(client.get("token_uri", "https://oauth2.googleapis.com/token"), {
        "client_id": client["client_id"], "client_secret": client["client_secret"],
        "refresh_token": token["refresh_token"], "grant_type": "refresh_token",
    })
    token.update(refreshed)
    keyring.set_password(SERVICE, TOKEN_ACCOUNT, json.dumps(token))
    return token


def google_user_info(access_token: str) -> dict[str, Any]:
    req = urllib.request.Request(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode())


def test_google(required_scope: str | list[str] | None = None) -> dict[str, Any]:
    token = _get_token()
    granted = set(token.get("scopes", []))
    required = [required_scope] if isinstance(required_scope, str) else (required_scope or [])
    missing = [scope for scope in required if scope not in granted]
    if missing:
        raise RuntimeError(f"Google did not grant required scope(s): {', '.join(missing)}. Reconnect and approve them.")
    return google_user_info(token["access_token"])


def test_gmail_connection() -> dict[str, Any]:
    """Verify Gmail API access with the read-only profile endpoint."""
    token = _get_token()
    if GMAIL_READONLY not in set(token.get("scopes", [])):
        raise RuntimeError("Gmail read permission is missing. Reconnect Gmail and approve inbox read access.")
    profile = _gmail_get("https://gmail.googleapis.com/gmail/v1/users/me/profile", token["access_token"])
    return {
        "email": profile.get("emailAddress", "Google account"),
        "messages_total": profile.get("messagesTotal"),
        "threads_total": profile.get("threadsTotal"),
    }


def test_calendar_connection() -> dict[str, Any]:
    """Verify Google Calendar API access by reading the primary calendar."""
    token = _get_token()
    if CALENDAR_EVENTS not in set(token.get("scopes", [])):
        raise RuntimeError("Google Calendar permission is missing. Reconnect Calendar and approve event access.")
    request = urllib.request.Request(
        "https://www.googleapis.com/calendar/v3/calendars/primary",
        headers={"Authorization": f"Bearer {token['access_token']}", "User-Agent": "Sentience/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            calendar = json.loads(response.read().decode())
    except Exception as exc:
        detail = exc.read().decode(errors="replace")[:400] if hasattr(exc, "read") else str(exc)
        raise RuntimeError(f"Google Calendar API request failed: {detail}") from exc
    return {"id": calendar.get("id"), "summary": calendar.get("summary", "Primary calendar")}


def _gmail_get(endpoint: str, access_token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        endpoint,
        headers={"Authorization": f"Bearer {access_token}", "User-Agent": "Sentience/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except Exception as exc:
        detail = exc.read().decode(errors="replace")[:500] if hasattr(exc, "read") else str(exc)
        raise RuntimeError(f"Gmail API request failed: {detail}") from exc


def search_emails(query: str = "", max_results: int = 5) -> list[dict[str, Any]]:
    """Search and read inbox messages without changing their state."""
    import base64
    import re
    from html import unescape

    token = _get_token()
    if GMAIL_READONLY not in set(token.get("scopes", [])):
        raise RuntimeError("Gmail read permission is missing. Reconnect Gmail and approve inbox read access.")
    count = max(1, min(int(max_results or 5), 10))
    search_query = query.strip() or "in:inbox"
    params = urllib.parse.urlencode({"q": search_query, "maxResults": count})
    listing = _gmail_get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages?{params}", token["access_token"])

    def decode_body(value: str) -> str:
        try:
            raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
            return raw.decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            return ""

    def collect_parts(payload: dict[str, Any], plain: list[str], html_parts: list[str]) -> None:
        mime = str(payload.get("mimeType", "")).casefold()
        body = payload.get("body", {})
        if isinstance(body, dict) and body.get("data"):
            decoded = decode_body(body["data"])
            if mime == "text/plain":
                plain.append(decoded)
            elif mime == "text/html":
                html_parts.append(decoded)
        for part in payload.get("parts", []) if isinstance(payload.get("parts", []), list) else []:
            if isinstance(part, dict):
                collect_parts(part, plain, html_parts)

    messages: list[dict[str, Any]] = []
    for item in listing.get("messages", [])[:count]:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        message = _gmail_get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{urllib.parse.quote(item['id'], safe='')}?format=full",
            token["access_token"],
        )
        payload = message.get("payload") or {}
        headers = {
            str(header.get("name", "")).casefold(): str(header.get("value", ""))
            for header in payload.get("headers", []) if isinstance(header, dict)
        }
        plain: list[str] = []
        html_parts: list[str] = []
        if isinstance(payload, dict):
            collect_parts(payload, plain, html_parts)
        body_text = "\n".join(plain).strip()
        if not body_text and html_parts:
            body_text = unescape(re.sub(r"<[^>]+>", " ", " ".join(html_parts)))
            body_text = re.sub(r"\s+", " ", body_text).strip()
        messages.append({
            "id": message.get("id"),
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "date": headers.get("date", ""),
            "subject": headers.get("subject", "(no subject)"),
            "snippet": message.get("snippet", ""),
            "body": body_text[:6000],
        })
    return messages


def send_email(to: str, subject: str, body: str) -> dict[str, Any]:
    """Send one email through Gmail API. Caller must obtain user confirmation."""
    import base64
    from email.message import EmailMessage

    token = _get_token()
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")
    request = urllib.request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        data=json.dumps({"raw": raw}).encode(),
        headers={"Authorization": f"Bearer {token['access_token']}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def create_calendar_event(
    summary: str,
    start: str,
    end: str,
    timezone: str,
    description: str = "",
    attendees: list[str] | None = None,
) -> dict[str, Any]:
    """Create an event; caller must obtain explicit user confirmation."""
    token = _get_token()
    event: dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start, "timeZone": timezone},
        "end": {"dateTime": end, "timeZone": timezone},
        "description": description,
    }
    if attendees:
        event["attendees"] = [{"email": address} for address in attendees]
    request = urllib.request.Request(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        data=json.dumps(event).encode(),
        headers={"Authorization": f"Bearer {token['access_token']}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise RuntimeError(f"Google Calendar returned HTTP {exc.code}: {detail}") from exc
