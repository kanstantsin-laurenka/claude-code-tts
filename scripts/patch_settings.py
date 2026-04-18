"""
PATCH_SETTINGS

Idempotent editor for ~/.claude/settings.json that installs / removes
Stop and SessionStart hooks for claude-code-tts.

Filter criterion: any hook whose `command` string contains the current
repo path OR the legacy `/.claude/skills/tts/` is treated as ours. This
makes the operation safe to re-run and automatically migrates installs
from the previous location.

Usage:
  patch_settings.py install --repo-dir /abs/path/to/claude-code-tts
  patch_settings.py uninstall --repo-dir /abs/path/to/claude-code-tts
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

LEGACY_MARKER = "/.claude/skills/tts/"
STOP_TIMEOUT_MS = 5000
SESSION_TIMEOUT_MS = 3000


def _is_ours(command: str, repo_dir: str) -> bool:
    if not isinstance(command, str):
        return False
    return repo_dir in command or LEGACY_MARKER in command


def _filter_hooks(entries: list[dict], repo_dir: str) -> list[dict]:
    """Drop any top-level hook entry whose inner command is ours; keep the rest."""
    result = []
    for entry in entries:
        inner = entry.get("hooks", []) if isinstance(entry, dict) else []
        if any(_is_ours(h.get("command", ""), repo_dir) for h in inner if isinstance(h, dict)):
            continue
        result.append(entry)
    return result


def _make_stop_entry(repo_dir: str) -> dict:
    return {
        "hooks": [
            {
                "type": "command",
                "command": f"bash {repo_dir}/hooks/tts-stop.sh",
                "timeout": STOP_TIMEOUT_MS,
            }
        ]
    }


def _make_session_entry(repo_dir: str) -> dict:
    return {
        "hooks": [
            {
                "type": "command",
                "command": f"bash {repo_dir}/hooks/tts-status.sh",
                "timeout": SESSION_TIMEOUT_MS,
            }
        ]
    }


def patch(settings: dict, repo_dir: str, action: str) -> dict:
    hooks = settings.setdefault("hooks", {})
    for key in ("Stop", "SessionStart"):
        hooks[key] = _filter_hooks(hooks.get(key, []) or [], repo_dir)

    if action == "install":
        hooks["Stop"].append(_make_stop_entry(repo_dir))
        hooks["SessionStart"].append(_make_session_entry(repo_dir))

    # Prune now-empty arrays to keep settings.json tidy.
    for key in ("Stop", "SessionStart"):
        if not hooks.get(key):
            hooks.pop(key, None)
    if not hooks:
        settings.pop("hooks", None)

    return settings


def _atomic_write(path: Path, data: str) -> None:
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".settings.", suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["install", "uninstall"])
    p.add_argument("--repo-dir", required=True, help="Absolute path to claude-code-tts repo")
    p.add_argument("--settings", default=str(Path.home() / ".claude" / "settings.json"))
    p.add_argument(
        "--backup", action="store_true", help="Save settings.json.bak-<ts> before writing"
    )
    args = p.parse_args(argv)

    repo_dir = os.path.abspath(args.repo_dir)
    settings_path = Path(args.settings).expanduser()

    if settings_path.exists():
        with open(settings_path, encoding="utf-8") as f:
            settings = json.load(f)
    else:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings = {}

    if args.backup and settings_path.exists():
        ts = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(settings_path, settings_path.with_suffix(f".json.bak-{ts}"))

    patched = patch(settings, repo_dir, args.action)

    # Round-trip JSON to catch corruption before atomic replace.
    serialized = json.dumps(patched, ensure_ascii=False, indent=2) + "\n"
    json.loads(serialized)  # raises if invalid

    _atomic_write(settings_path, serialized)
    print(f"settings.json {args.action}ed for {repo_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
