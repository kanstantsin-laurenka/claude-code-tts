#!/bin/bash
# Developer setup: install dev deps + enable the repo-local git hooks.
# Re-runnable. Intended to be run once per fresh clone.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

if [[ ! -d "venv" ]]; then
    python3 -m venv venv
    echo "dev-setup: created venv"
fi
venv/bin/pip install --quiet --upgrade pip
venv/bin/pip install --quiet -r requirements.txt -r requirements-dev.txt
echo "dev-setup: deps installed (runtime + dev)"

# Point git at the repo-tracked hooks dir so pre-push runs the test suite.
git config core.hooksPath .githooks
echo "dev-setup: core.hooksPath set to .githooks (pre-push now active)"
