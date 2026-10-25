"""Prompt helpers shared by every interactive CLI screen."""

from __future__ import annotations

import os
from typing import Any

from prompt_toolkit import prompt as _prompt
from prompt_toolkit.completion import Completer, Completion
from rich.console import Console


class _NavigationCompleter(Completer):
    """Make the navigation commands available at every nested prompt."""

    def get_completions(self, document, complete_event):
        word = document.get_word_before_cursor().casefold()
        for value in ("back", "clear", "home"):
            if not word or value.startswith(word):
                yield Completion(value, start_position=-len(word))


_completer = _NavigationCompleter()
_clear_header: Any = None
_screen_snapshot: list[tuple[Console, tuple[Any, ...], dict[str, Any]]] = []
_pending_screen: list[tuple[Console, tuple[Any, ...], dict[str, Any]]] = []
_replaying_screen = False
_console_print = Console.print


class HomeRequested(Exception):
    """Raised by a nested prompt to return directly to the Sentience root."""


def _record_console_print(self: Console, *objects: Any, **kwargs: Any) -> Any:
    if not _replaying_screen:
        _pending_screen.append((self, objects, dict(kwargs)))
    return _console_print(self, *objects, **kwargs)


# CLI menus use module-level Rich Console objects across command modules. Keep
# their last rendered screen so a nested clear can redraw the active menu.
Console.print = _record_console_print


def set_clear_header(renderer: Any) -> None:
    """Set the shell-owned header renderer used after clearing nested screens."""
    global _clear_header
    _clear_header = renderer


def reset_prompt_screen() -> None:
    """Forget prior command output before the shell dispatches a new command."""
    global _screen_snapshot, _pending_screen
    _screen_snapshot = []
    _pending_screen = []


def _redraw_current_screen() -> None:
    global _replaying_screen
    _replaying_screen = True
    try:
        if _clear_header is not None:
            _clear_header()
        for console, objects, kwargs in _screen_snapshot:
            console.print(*objects, **kwargs)
    finally:
        _replaying_screen = False


def clear_screen(redraw: Any = None) -> None:
    """Clear the terminal and redraw the Sentience header and active screen."""
    os.system("cls" if os.name == "nt" else "clear")
    if redraw is None:
        _redraw_current_screen()
        return
    global _replaying_screen
    _replaying_screen = True
    try:
        if _clear_header is not None:
            _clear_header()
        redraw()
    finally:
        _replaying_screen = False


def prompt(message: str = "", *args: Any, **kwargs: Any) -> str:
    """Prompt with global navigation completion and clear/home handling."""
    handle_clear = kwargs.pop("handle_clear", True)
    handle_home = kwargs.pop("handle_home", True)
    kwargs.setdefault("completer", _completer)
    global _screen_snapshot, _pending_screen
    if _pending_screen:
        _screen_snapshot = _pending_screen
        _pending_screen = []
    while True:
        value = _prompt(message, *args, **kwargs)
        command = value.strip().casefold()
        if command == "home" and handle_home:
            raise HomeRequested
        if command != "clear" or not handle_clear:
            return value
        clear_screen()
