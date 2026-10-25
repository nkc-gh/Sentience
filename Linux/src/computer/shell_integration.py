"""
Shell Command Integration.

Generates shell hook scripts for bash / zsh / fish that log every
executed command (with timestamp, exit code, working directory, and
duration) to a local JSONL file.

A background reader thread tails that file and writes new entries into
the Memory database.
"""

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from config import SentienceConfig
from storage.database import Database
from storage.models import ShellCommand

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# Hook script templates
# ═══════════════════════════════════════════════════════════

BASH_HOOK = r'''# SENTIENCE Shell Integration (bash)
# Source this file in your ~/.bashrc

_sentience_preexec() {{
    _SENTIENCE_START=$EPOCHREALTIME
    _SENTIENCE_CMD="$1"
}}
trap '_sentience_preexec "$BASH_COMMAND"' DEBUG

_sentience_precmd() {{
    local ec=$?
    [[ -z "$_SENTIENCE_CMD" ]] && return
    local end=$EPOCHREALTIME
    local dur=$(echo "($end - $_SENTIENCE_START) * 1000" | bc 2>/dev/null || echo 0)
    printf '{{"shell":"bash","command":"%s","cwd":"%s","exit_code":%d,"timestamp":"%s","duration_ms":%.0f}}\n' \
        "$(echo "$_SENTIENCE_CMD" | sed 's/"/\\"/g')" \
        "$PWD" "$ec" "$(date -Iseconds)" "$dur" \
        >> "{log_path}"
    unset _SENTIENCE_CMD
}}
PROMPT_COMMAND="_sentience_precmd;${{PROMPT_COMMAND}}"
'''

ZSH_HOOK = r'''# SENTIENCE Shell Integration (zsh)
# Source this file in your ~/.zshrc

_sentience_preexec() {{
    _SENTIENCE_START=$EPOCHREALTIME
    _SENTIENCE_CMD="$1"
}}

_sentience_precmd() {{
    local ec=$?
    [[ -z "$_SENTIENCE_CMD" ]] && return
    local end=$EPOCHREALTIME
    local dur=$(( (end - _SENTIENCE_START) * 1000 ))
    printf '{{"shell":"zsh","command":"%s","cwd":"%s","exit_code":%d,"timestamp":"%s","duration_ms":%.0f}}\n' \
        "${{_SENTIENCE_CMD//\"/\\\"}}" \
        "$PWD" "$ec" "$(date -Iseconds)" "$dur" \
        >> "{log_path}"
    unset _SENTIENCE_CMD
}}

autoload -Uz add-zsh-hook
add-zsh-hook preexec _sentience_preexec
add-zsh-hook precmd _sentience_precmd
'''

FISH_HOOK = r'''# SENTIENCE Shell Integration (fish)
# Source this file in your ~/.config/fish/config.fish

function _sentience_preexec --on-event fish_preexec
    set -g _SENTIENCE_START (date +%s%N)
    set -g _SENTIENCE_CMD $argv[1]
end

function _sentience_postexec --on-event fish_postexec
    set -l ec $status
    if set -q _SENTIENCE_CMD
        set -l now (date +%s%N)
        set -l dur (math "($now - $_SENTIENCE_START) / 1000000")
        echo '{{"shell":"fish","command":"'(string replace -a '"' '\\"' -- $_SENTIENCE_CMD)'","cwd":"'$PWD'","exit_code":'$ec',"timestamp":"'(date -Iseconds)'","duration_ms":'$dur'}}' \
            >> "{log_path}"
        set -e _SENTIENCE_CMD
    end
end
'''


# ═══════════════════════════════════════════════════════════
# Shell Integration
# ═══════════════════════════════════════════════════════════


class ShellIntegration:
    """Install shell hooks and tail the resulting log file into the DB."""

    def __init__(self, db: Database, config: SentienceConfig):
        self.db = db
        self.config = config
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_position: int = 0

    # ── Hook generation ──────────────────────────────────

    def install_hooks(self) -> Dict[str, str]:
        """Write hook scripts and return ``{shell: path}``."""
        hooks = self.config.hooks_dir
        hooks.mkdir(parents=True, exist_ok=True)
        log = str(self.config.shell_log_path)

        paths: Dict[str, str] = {}

        bash_path = hooks / "sentience.bash"
        bash_path.write_text(BASH_HOOK.format(log_path=log))
        paths["bash"] = str(bash_path)

        zsh_path = hooks / "sentience.zsh"
        zsh_path.write_text(ZSH_HOOK.format(log_path=log))
        paths["zsh"] = str(zsh_path)

        fish_path = hooks / "sentience.fish"
        fish_path.write_text(FISH_HOOK.format(log_path=log))
        paths["fish"] = str(fish_path)

        logger.info("Shell hooks installed at %s", hooks)
        return paths

    def get_source_instructions(self) -> str:
        hooks = self.config.hooks_dir
        return (
            "Add one of the following to your shell config:\n\n"
            f"  Bash (~/.bashrc):  source {hooks}/sentience.bash\n"
            f"  Zsh  (~/.zshrc):   source {hooks}/sentience.zsh\n"
            f"  Fish (~/.config/fish/config.fish):  source {hooks}/sentience.fish\n"
        )

    # ── Log reader ───────────────────────────────────────

    def start_reader(self) -> None:
        if self._running:
            return
        self.config.shell_log_path.touch(exist_ok=True)
        self._running = True
        self._thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()
        logger.info("Shell log reader started")

    def stop_reader(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Shell log reader stopped")

    def _reader_loop(self) -> None:
        log_path = self.config.shell_log_path
        while self._running:
            try:
                if log_path.exists():
                    with open(log_path, "r") as fh:
                        fh.seek(self._last_position)
                        for line in fh:
                            line = line.strip()
                            if line:
                                self._process_line(line)
                        self._last_position = fh.tell()
            except Exception:
                logger.exception("Shell log read error")
            time.sleep(2.0)

    def _process_line(self, line: str) -> None:
        try:
            data = json.loads(line)
            cmd = ShellCommand(
                shell_type=data.get("shell", "unknown"),
                command=data.get("command", ""),
                working_directory=data.get("cwd", ""),
                exit_code=data.get("exit_code"),
                timestamp=(
                    datetime.fromisoformat(data["timestamp"])
                    if "timestamp" in data
                    else datetime.now()
                ),
                duration_seconds=(
                    data["duration_ms"] / 1000.0
                    if "duration_ms" in data
                    else None
                ),
            )
            if cmd.command:
                self.db.add_shell_command(cmd)
                logger.debug("Recorded shell: %s", cmd.command[:60])
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.debug("Bad shell log line: %s", exc)
