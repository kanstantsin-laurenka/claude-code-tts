#!/bin/bash
# Claude Code TTS uninstaller: reverse of install.sh.
# Use --purge to also delete state file, venv, and logs.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_TARGET="$HOME/Library/LaunchAgents/com.claude-code.tts.plist"
AGENT_LABEL="com.claude-code.tts"
UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"
SERVICE="${DOMAIN}/${AGENT_LABEL}"
SETTINGS_JSON="$HOME/.claude/settings.json"
STATE_DIR="$HOME/.config/claude-code-tts"
SYMLINK="$HOME/.local/bin/tts"

PURGE=0
if [[ "${1:-}" == "--purge" ]]; then
    PURGE=1
fi

say()  { printf '\033[0;32m✓\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$1"; }

# 1. launchd
if launchctl print "$SERVICE" >/dev/null 2>&1; then
    launchctl bootout "$DOMAIN" "$PLIST_TARGET" 2>/dev/null || true
    say "launchd agent booted out"
else
    say "launchd agent not loaded"
fi
# Clear a leftover `tts autostart off` so a later reinstall starts from the default.
launchctl enable "$SERVICE" 2>/dev/null || true
rm -f "$PLIST_TARGET"
say "plist removed"

# 2. settings.json hooks
if [[ -f "$SETTINGS_JSON" ]]; then
    if [[ -x "$REPO_DIR/venv/bin/python" ]]; then
        PY="$REPO_DIR/venv/bin/python"
    else
        PY="python3"
    fi
    "$PY" "$REPO_DIR/scripts/patch_settings.py" \
        uninstall --repo-dir "$REPO_DIR" --settings "$SETTINGS_JSON" --backup
    say "settings.json hooks removed (backup saved)"
fi

# 3. CLI symlink
if [[ -L "$SYMLINK" ]]; then
    target="$(readlink "$SYMLINK")"
    if [[ "$target" == "$REPO_DIR/bin/tts" ]]; then
        rm -f "$SYMLINK"
        say "symlink ~/.local/bin/tts removed"
    else
        warn "~/.local/bin/tts points elsewhere ($target) — left alone"
    fi
fi

# 4. Purge (optional)
if [[ "$PURGE" -eq 1 ]]; then
    rm -rf "$STATE_DIR"
    rm -rf "$REPO_DIR/venv" "$REPO_DIR/logs"
    say "purged state, venv, and logs"
else
    say "state kept at $STATE_DIR (use --purge to remove)"
fi

echo
echo "Uninstall complete. Repo directory left at $REPO_DIR — remove manually if desired."
