#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${SENTIENCE_REPO_URL:-https://github.com/nkc-gh/Sentience.git}"
INSTALL_ROOT="${SENTIENCE_INSTALL_DIR:-${HOME}/.local/share/sentience}"
BIN_DIR="${SENTIENCE_BIN_DIR:-${HOME}/.local/bin}"

command -v git >/dev/null 2>&1 || { echo "Git is required. Install Git and run this command again." >&2; exit 1; }
command -v python3.13 >/dev/null 2>&1 || { echo "CPython 3.13.x is required. Install it and run this command again." >&2; exit 1; }
python3.13 -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)' || { echo "CPython 3.13.x is required." >&2; exit 1; }

mkdir -p "${INSTALL_ROOT}" "${BIN_DIR}"
if [ -d "${INSTALL_ROOT}/source/.git" ]; then
    git -C "${INSTALL_ROOT}/source" pull --ff-only
else
    rm -rf "${INSTALL_ROOT}/source"
    git clone --depth 1 "${REPO_URL}" "${INSTALL_ROOT}/source"
fi

PROJECT="${INSTALL_ROOT}/source/Linux"
if [ ! -f "${PROJECT}/pyproject.toml" ]; then
    echo "Linux project was not found in ${REPO_URL}" >&2
    exit 1
fi

if [ -x "${INSTALL_ROOT}/venv/bin/python" ] && ! "${INSTALL_ROOT}/venv/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)'; then
    rm -rf "${INSTALL_ROOT}/venv"
fi
python3.13 -m venv "${INSTALL_ROOT}/venv"
"${INSTALL_ROOT}/venv/bin/python" -m pip install --upgrade pip
"${INSTALL_ROOT}/venv/bin/python" -m pip install -e "${PROJECT}[dev]"
ln -sfn "${INSTALL_ROOT}/venv/bin/sentience" "${BIN_DIR}/sentience"

echo "Sentience Linux installed. Run: ${BIN_DIR}/sentience"
