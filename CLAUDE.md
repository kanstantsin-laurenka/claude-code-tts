# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

`claude-code-tts` speaks Claude Code responses aloud on macOS using Microsoft's free neural voices through [edge-tts](https://github.com/rany2/edge-tts). Data flow: Claude Code `Stop` hook → `hooks/tts-stop.sh` → HTTP POST to `127.0.0.1:7852` → local Python server (launchd-managed) → `clean_text` → edge-tts → `afplay`.

Architecture and config details live in `@docs/ARCHITECTURE.md` and `@docs/CONFIGURATION.md`.

## Platform constraint

**macOS only.** The tool relies on `launchd`, `afplay`, and `launchctl`. Do not add Linux/Windows shims or abstraction layers — cross-platform support is explicitly out of scope.

## Layout

- `bin/tts` — Bash CLI (`on`/`off`/`toggle`/`status`/`test`/`restart`/`voice`/...)
- `src/claude_code_tts/` — Python package: `server.py` (HTTP + edge-tts), `clean_text.py` (markdown cleanup)
- `hooks/` — Bash hooks registered in `~/.claude/settings.json` (`tts-stop.sh`, `tts-status.sh`)
- `scripts/patch_settings.py` — idempotent editor for `~/.claude/settings.json`
- `launchd/` — `launchd` plist template
- `tests/` — pytest suite (tests `clean_text`, `patch_settings`, `server`)
- `install.sh` / `uninstall.sh` — idempotent, re-runnable

## Development commands

All Python work runs inside the repo-local venv created by `install.sh`:

```bash
venv/bin/pip install -r requirements-dev.txt   # dev deps (pytest, ruff)
venv/bin/pytest tests/ -v                      # run tests
venv/bin/ruff format .                         # format
venv/bin/ruff check .                          # lint
```

CI runs ruff lint + pytest on push/PR across Python 3.10 / 3.11 / 3.12 / 3.13 — see `.github/workflows/test.yml`.

Use the `/verify` skill to run lint + format-check + tests in one shot before marking work done.

## Code style

- Python 3.10+ target, `src/` layout (package under `src/claude_code_tts/`)
- Formatter/linter: **ruff** — line-length 100, target `py310`. Config in `pyproject.toml`.
- Bash: `set -euo pipefail` at the top of every script; keep `bin/tts` readable — no clever one-liners.

## Critical rules

- **Never write to `~/.claude/settings.json` directly.** Use `scripts/patch_settings.py` — it backs up first and is idempotent. Direct edits break the install/uninstall contract.
- **Do not run `bin/tts install` / `bin/tts on` / `install.sh` during dev work** unless the user asks. These patch the user's real `~/.claude/settings.json` and register a launchd agent — side effects persist across sessions.
- **Don't touch `.claude/settings.local.json`** — it's personal and git-ignored globally.
- **`install.sh` must stay idempotent**, including the legacy-migration path from `~/.claude/skills/tts/`. If you change the install flow, re-run it mentally against a fresh install, an existing install, and a legacy install.

## Git workflow

- One task = one worktree = one branch = one PR. Create with `git worktree add ../claude-code-tts-<short-name> <type>/<short-desc>`.
- Branch prefix: `feat/`, `fix/`, `chore/`, `refactor/`.
- Never commit directly to `main`. Open a PR and let CI (`pytest` on 3.10–3.13) pass before merge.
- Commit style matches the existing log: `type: short description` (see `git log --oneline`).
