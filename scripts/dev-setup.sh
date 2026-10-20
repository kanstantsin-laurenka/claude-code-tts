#!/bin/bash
# Developer setup: venv with runtime + dev deps from uv.lock, then the repo-local git hooks.
# Re-runnable. Intended to be run once per fresh clone or worktree.
#
# uv picks the interpreter from .python-version (downloading it if missing) instead of
# `python3` from PATH, which on macOS is often the too-old system 3.9. An existing venv
# on a different Python is recreated by uv sync.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

if ! command -v uv >/dev/null 2>&1; then
    echo "dev-setup: uv not found — install it: brew install uv" >&2
    exit 1
fi

# The repo keeps its venv at venv/ (not uv's default .venv/): the launchd plist,
# the pre-push hook and install.sh all point there.
UV_PROJECT_ENVIRONMENT="$REPO_DIR/venv" uv sync --locked
echo "dev-setup: venv synced with uv.lock ($(venv/bin/python -V))"

# Point git at the repo-tracked hooks dir so pre-push runs the test suite.
git config core.hooksPath .githooks
echo "dev-setup: core.hooksPath set to .githooks (pre-push now active)"
