# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

`claude-code-tts` speaks Claude Code responses aloud on macOS. Default voice is Microsoft's free neural voices through [edge-tts](https://github.com/rany2/edge-tts); `openai`, `google` (Chirp 3 HD) and `yandex` (SpeechKit v3) are optional providers. Data flow: Claude Code `Stop` hook → `hooks/tts-stop.sh` → HTTP POST to `127.0.0.1:7852` → local Python server (launchd-managed) → `clean_text` → TTS provider → `afplay`.

On/off state = the launchd agent being enabled and loaded (no flag file); hooks only check `/health`. Details: `docs/CONFIGURATION.md#on--off`.

Architecture and config details live in `@docs/ARCHITECTURE.md` and `@docs/CONFIGURATION.md`.

## Platform constraint

**macOS only.** The tool relies on `launchd`, `afplay`, and `launchctl`. Do not add Linux/Windows shims or abstraction layers — cross-platform support is explicitly out of scope.

## Layout

- `bin/tts` — Bash CLI (`on`/`off`/`toggle`/`status`/`test`/`restart`/`voice`/...)
- `src/claude_code_tts/` — Python package: `server.py`, `clean_text.py`, `keychain.py` (read-only), `usage.py`, `notify.py`
- `src/claude_code_tts/providers/` — `edge.py`, `openai.py`, `google.py` (REST client), `google_fallback.py` (quota alerts + fallback to edge-tts), `yandex.py` (SpeechKit v3 REST, no fallback)
- Google monthly quota (Pacific month, 990k limit, sticky switch to edge-tts): see `docs/CONFIGURATION.md#google-knobs`. All policy lives in `google_fallback.py`, not in `google.py` or `usage.py`.
- `hooks/` — Bash hooks registered in `~/.claude/settings.json` (`tts-stop.sh`, `tts-status.sh`)
- `scripts/patch_settings.py` — idempotent editor for `~/.claude/settings.json`
- `launchd/` — `launchd` plist template
- `tests/` — pytest suite; CI runs on Linux, so tests must not need `security`/`afplay`/`osascript`
  - `tests/test_cli_onoff.py` runs the real `bin/tts` and hooks against stub `launchctl`/`curl`/PlistBuddy (`PLISTBUDDY` env override) — extend it when changing on/off or config-command behavior.
- `docs/plans/` — design docs for larger features (limits, alert numbers, locked decisions), e.g. `google-chirp-provider.md`
- `install.sh` / `uninstall.sh` — idempotent, re-runnable
- `scripts/dev-setup.sh` — one-shot contributor setup (`uv sync` into `venv/`, git hooks)
- `pyproject.toml` + `uv.lock` — dependencies (runtime + `dev` group) and pinned versions; `.python-version` — dev Python. There is no `requirements.txt`: without uv, `install.sh` reads the ranges from `pyproject.toml`
- `.githooks/pre-push` — runs `pytest -q` before `git push`; opt-in via `scripts/dev-setup.sh`

## Development commands

One-time setup on a fresh clone:

```bash
brew install uv        # once; dev setup requires uv
scripts/dev-setup.sh    # uv sync into venv/ (runtime + dev deps) + enables .githooks/pre-push via core.hooksPath
```

Day-to-day Python work runs inside the repo-local venv:

```bash
venv/bin/pytest tests/ -v                      # run tests
venv/bin/ruff format .                         # format
venv/bin/ruff check .                          # lint
```

The `pre-push` hook runs the full pytest suite before any `git push`; bypass with `git push --no-verify`. The hook is only active after `scripts/dev-setup.sh` sets `core.hooksPath=.githooks`.

Dependencies change in `pyproject.toml`, never by `pip install` into the venv. Afterwards run `uv lock`, then `scripts/dev-setup.sh`. CI (`uv sync --locked`) fails if `uv.lock` is stale.

CI runs ruff lint + pytest on push/PR across Python 3.11 / 3.12 / 3.13 / 3.14 via uv — see `.github/workflows/test.yml`. Its `automerge` job merges green Dependabot minor/patch PRs. **INVARIANT:** it must never merge `edge-tts`, majors, or maintainer changes — `edge-tts` skips the Dependabot cooldown on that promise (README → Dependency updates).

Use the `/verify` skill to run lint + format-check + tests in one shot before marking work done. A fresh worktree has no `venv/`: run `scripts/dev-setup.sh` in it first.

## Code style

- Python 3.11+ target (`requires-python`); dev on 3.14 (`.python-version`), `src/` layout (package under `src/claude_code_tts/`)
- Formatter/linter: **ruff** — line-length 100, target `py311`. Config in `pyproject.toml`.
- Bash: `set -euo pipefail` at the top of every script; keep `bin/tts` readable — no clever one-liners.
- `bin/tts` runs on macOS `/bin/bash` 3.2: no associative arrays, `${var,,}`, or `local -n`.

## Critical rules

- **Never write to `~/.claude/settings.json` directly.** Use `scripts/patch_settings.py` — it backs up first and is idempotent. Direct edits break the install/uninstall contract.
- **Do not run `bin/tts install` / `bin/tts on` / `bin/tts off` / `install.sh` during dev work** unless the user asks. `install` patches the user's real `~/.claude/settings.json`; all of them register, enable or disable the real launchd agent — side effects persist across sessions and reboots.
- **INVARIANT: config commands must not start a stopped server.** New `tts <setting>` commands apply changes via `_reload_agent`, which only writes the plist while TTS is off.
- **⚠️ WARNING: `launchctl enable` before `bootstrap`.** launchd refuses to load a disabled service, and `tts off` disables it — any code path that loads the agent must enable it first.
- **Keychain writes happen only in `bin/tts`**, via `security … -w` prompting on the tty; `keychain.py` is read-only. Never pass a key via argv, and never touch the real Keychain items `-a openai` / `-a google` / `-a yandex` in experiments.
- **Interactive commands (`tts google-key`, `tts openai-key`, `tts yandex-key`) need a real tty.** `!` in Claude Code has none — test them in a normal terminal.
- **New `GOOGLE_*`/`OPENAI_*`/`YANDEX_*` env vars need no plist template or `install.sh` change** — `install.sh` preserves `EnvironmentVariables` generically.
- **Don't touch `.claude/settings.local.json`** — it's personal and git-ignored globally.
- **`install.sh` must stay idempotent**, including the legacy-migration path from `~/.claude/skills/tts/`, and must preserve the current on/off state on re-run (a first install defaults to off; an old `enabled` flag file is read once, then deleted). If you change the install flow, re-run it mentally against a fresh install, an existing install, and a legacy install.

## Git workflow

- One task = one worktree = one branch = one PR. Create with `git worktree add -b <type>/<short-desc> ../claude-code-tts-<short-name> origin/main`.
- Branch prefix: `feat/`, `fix/`, `chore/`, `refactor/`.
- Never commit directly to `main`. Open a PR and let CI (`pytest` on 3.11–3.14) pass before merge.
- Commit style matches the existing log: `type(scope): short description`, e.g. `feat(google): …` (see `git log --oneline`).
