# Sentience

Sentience is a privacy-first personal AI agent for your computer. It is a terminal-based assistant that combines AI chat, local computer awareness, file history, model routing, memory, privacy controls, and approved external connectors in one application.

Sentience is designed to keep the user in control. Local files, activity history, credentials, model choices, and external actions are governed by explicit permissions and Sentinel privacy policies.

**Website:** [nkc-gh.github.io/Sentience](https://nkc-gh.github.io/Sentience/)

## Features

- Interactive `sentience >` terminal shell.
- Local and cloud AI model support.
- Task-based model routing for chat, reasoning, coding, and embeddings.
- Approved workspace and file access.
- Argus file tracking, Git-based history, and change diffs.
- Local memory, document extraction, search, and optional RAG retrieval.
- Sentinel secret detection, redaction, and local-only routing.
- Shell command history and terminal integration.
- Foreground application and window activity tracking.
- Skills, learned preferences, conversations, and audit records.
- Approved connectors for services such as Gmail, Calendar, flights, and trains.
- Local daemon for background coordination and health monitoring.

## Platform support

The project contains two platform implementations:

- `Linux/` — Linux implementation with XDG paths, Unix sockets, Bash/Zsh/Fish integration, and GNOME Wayland window tracking.
- `Windows/` — Windows implementation with native application-data paths, localhost TCP, PowerShell integration, User32 window tracking, and Git for Windows support.

## Global installation

The commands below download the project from the public GitHub repository, create an isolated Python environment, install Sentience, and create a launcher.

### Linux

Requirements: 64-bit CPython 3.13.x, Git, and a terminal. Install these prerequisites before running the installer.

```bash
curl -fsSL https://raw.githubusercontent.com/nkc-gh/Sentience/main/Linux/install.sh | bash
```

Then run:

```bash
sentience
```

If `sentience` is not found, add `~/.local/bin` to your `PATH` and restart the terminal.

### Windows

Requirements: 64-bit CPython 3.13.x, Git for Windows, and PowerShell. Install these prerequisites before running the installer.

```powershell
irm https://raw.githubusercontent.com/nkc-gh/Sentience/main/Windows/install.ps1 | iex
```

Using `curl.exe` directly:

```powershell
curl.exe -fsSL https://raw.githubusercontent.com/nkc-gh/Sentience/main/Windows/install.ps1 -o install.ps1; powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

Restart PowerShell after installation, then run:

```powershell
sentience
```

The installer commands use the public `nkc-gh/Sentience` repository. Set `SENTIENCE_REPO_URL` before running the installer if using a different fork.

## Run from source

### Linux

```bash
cd Linux
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
sentience
```

### Windows PowerShell

```powershell
cd Windows
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\sentience.exe
```

The direct `\.venv\Scripts\...` commands are intentional. PowerShell may block `Activate.ps1` because of the machine's execution policy, but activation is not required for Sentience. If you prefer an activated environment, enable scripts only for the current PowerShell window first:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
sentience
```

## First use

After starting Sentience:

1. Type `help` to view available commands.
2. Configure a local or cloud model with `model`.
3. Configure task routing with `routing`.
4. Review privacy behavior with `sentinel`.
5. Add approved workspaces with `archive`.
6. Start an AI session with `agent`.
7. Inspect file history with `argus` and window activity with `window`.

Cloud models and external connectors are optional. Sensitive data should be tested with synthetic values before enabling cloud routes.

## Project structure

```text
Sentience/
├── README.md
├── Linux/
│   ├── install.sh
│   ├── pyproject.toml
│   ├── src/
│   │   ├── cli/          Interactive shell, commands, and agent runtime
│   │   ├── computer/     Filesystem, Git, documents, shell, and windows
│   │   ├── config/       Platform-aware configuration
│   │   ├── connectors/   Approved external service connectors
│   │   ├── daemon/       Local daemon and health API
│   │   ├── memory/       Local retrieval, documents, and RAG
│   │   ├── routing/      Model and task routing
│   │   ├── sentinel/     Privacy scanning and policy
│   │   ├── skills/       Built-in and user skills
│   │   └── storage/      SQLite database and models
│   └── tests/             Linux/shared regression tests
└── Windows/
    ├── install.ps1
    ├── pyproject.toml
    ├── src/               Windows-adapted implementation
    └── tests/              Shared and Windows-specific tests
```

## Development checks

From either platform directory:

```bash
python -m pytest -q
python -m compileall -q src
```

The test suite covers the CLI, agent behavior, memory, routing, prompts, Sentinel, file history, and platform integrations. Windows-specific tests should be run on Windows.

Made by Team WC
