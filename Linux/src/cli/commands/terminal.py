"""Launch the configured shell as a real interactive child process."""

from __future__ import annotations

import fcntl
import os
import signal
import shlex
import shutil
import sys
import tempfile
import termios
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple

from rich.console import Console
from rich.panel import Panel

from storage.models import ShellCommand

if TYPE_CHECKING:
    from cli.shell import SentienceShell

console = Console()


def _configured_shell(shell: SentienceShell) -> Tuple[str, Path]:
    """Resolve the saved shell name/path and reject unusable executables."""
    name = shell.config.get_setting("terminal_shell_name", "bash").strip() or "bash"
    saved_path = shell.config.get_setting("terminal_shell_path", "").strip()
    candidate = Path(os.path.expandvars(os.path.expanduser(saved_path))) if saved_path else None
    if candidate is None or not candidate.exists():
        located = shutil.which(name)
        candidate = Path(located) if located else Path(f"/bin/{name}")
    try:
        candidate = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise FileNotFoundError(f"Configured shell was not found: {candidate}")
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise PermissionError(f"Configured shell is not an executable file: {candidate}")
    return name, candidate


def _startup_command(executable: Path, temp_dir: Path, env: dict) -> List[str]:
    """Build shell-specific startup so user rc files and native completion load."""
    shell_kind = executable.name

    if shell_kind == "bash":
        rc_file = temp_dir / "bashrc"
        user_rc = Path(env.get("HOME", str(Path.home()))) / ".bashrc"
        lines = ["# Sentience interactive shell startup"]
        if user_rc.is_file():
            lines.append(f"source {shlex.quote(str(user_rc))}")
        lines += ["function back() { builtin exit; }", "function home() { builtin exit; }", ""]
        rc_file.write_text("\n".join(lines), encoding="utf-8")
        return [str(executable), "--rcfile", str(rc_file), "-i"]

    if shell_kind in ("zsh", "zsh5"):
        # Zsh uses ZDOTDIR for its startup files. Source the user's normal
        # .zshenv and .zshrc from a temporary .zshrc that adds the back command.
        original_zdotdir = Path(env.get("ZDOTDIR") or env.get("HOME", str(Path.home())))
        zshenv = original_zdotdir / ".zshenv"
        zshrc = original_zdotdir / ".zshrc"
        lines = ["# Sentience interactive shell startup"]
        if zshenv.is_file():
            lines.append(f"source {shlex.quote(str(zshenv))}")
        if zshrc.is_file():
            lines.append(f"source {shlex.quote(str(zshrc))}")
        lines += ["function back { builtin exit }", "function home { builtin exit }", ""]
        (temp_dir / ".zshrc").write_text("\n".join(lines), encoding="utf-8")
        env["ZDOTDIR"] = str(temp_dir)
        return [str(executable), "-i"]

    if shell_kind == "fish":
        # -C runs after fish has loaded its normal config and before the prompt.
        return [str(executable), "-i", "-C", "function back; exit; end; function home; exit; end"]

    if shell_kind in ("sh", "dash", "ash", "ksh"):
        # POSIX-style shells read the file named by ENV in interactive mode.
        original_env = env.get("ENV", "")
        env_file = temp_dir / "env"
        lines = ["# Sentience interactive shell startup"]
        if original_env and Path(original_env).is_file():
            lines.append(f". {shlex.quote(original_env)}")
        lines += ["back() { exit; }", "home() { exit; }", ""]
        env_file.write_text("\n".join(lines), encoding="utf-8")
        env["ENV"] = str(env_file)
        return [str(executable), "-i"]

    # Custom shells still run interactively and load their normal startup.
    return [str(executable), "-i"]


def terminal_command(shell: SentienceShell, args: List[str]) -> None:
    """Run the configured shell with normal interactive terminal behavior."""
    try:
        shell_name, shell_path = _configured_shell(shell)
    except (FileNotFoundError, PermissionError) as exc:
        console.print(f"[red]Cannot start terminal shell:[/red] {exc}")
        console.print("[dim]Choose an installed executable in settings > Terminal Shell Config.[/dim]")
        return

    console.print()
    console.print(Panel(
        "[bold white]Interactive Terminal Mode[/bold white]\n"
        "[dim]Running your configured shell with its normal startup files, prompt, history, and completion.[/dim]\n\n"
        f"  [cyan]Working directory:[/cyan] [bold green]{os.getcwd()}[/bold green]\n"
        f"  [cyan]Active shell:[/cyan] [bold green]{shell_name}[/bold green] ([dim]{shell_path}[/dim])\n\n"
        "  [dim]Use this shell as usual. Type 'back' or 'home' to return to Sentience.[/dim]",
        border_style="green",
    ))

    start_time = time.time()
    return_code: Optional[int] = None
    env = os.environ.copy()
    try:
        with tempfile.TemporaryDirectory(prefix="sentience-shell-") as temp_name:
            argv = _startup_command(shell_path, Path(temp_name), env)
            # Give the interactive shell its own controlling pseudo-terminal.
            # This enables readline/fish/zsh completion, job control, Ctrl-C,
            # and full-screen terminal programs while keeping Sentience alive.
            import pty
            changed_env = [
                f"{key}={value}"
                for key, value in env.items()
                if os.environ.get(key) != value
            ]
            if changed_env:
                env_executable = shutil.which("env")
                if not env_executable:
                    raise FileNotFoundError("The 'env' utility is required to start this shell")
                argv = [env_executable, *changed_env, *argv]

            def sync_terminal_size(master_fd: int) -> None:
                try:
                    size = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, b"\0" * 8)
                    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, size)
                except (OSError, AttributeError):
                    pass

            def read_child(master_fd: int) -> bytes:
                master_fd_holder[0] = master_fd
                sync_terminal_size(master_fd)
                return os.read(master_fd, 1024)

            master_fd_holder: List[Optional[int]] = [None]

            def handle_resize(_signum, _frame) -> None:
                master_fd = master_fd_holder[0]
                if master_fd is not None:
                    sync_terminal_size(master_fd)

            previous_winch = signal.getsignal(signal.SIGWINCH)
            signal.signal(signal.SIGWINCH, handle_resize)
            try:
                status = pty.spawn(argv, master_read=read_child)
            finally:
                signal.signal(signal.SIGWINCH, previous_winch)
            return_code = os.waitstatus_to_exitcode(status)
    except KeyboardInterrupt:
        console.print("\n[dim]Shell interrupted; returning to Sentience.[/dim]")
    except OSError as exc:
        console.print(f"[red]Could not start {shell_name}:[/red] {exc}")
    finally:
        # Preserve the existing activity record behavior at session granularity.
        # Commands remain in the selected shell's own history file.
        try:
            shell.db.add_shell_command(ShellCommand(
                shell_type=shell_name,
                command="<interactive shell session>",
                working_directory=os.getcwd(),
                exit_code=return_code,
                timestamp=datetime.now(),
                duration_seconds=time.time() - start_time,
            ))
        except Exception:
            pass

    console.print("\n[dim]Returned to Sentience.[/dim]")
