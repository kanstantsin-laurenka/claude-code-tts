#!/bin/bash
# Claude Code TTS installer (idempotent).
# Migrates legacy ~/.claude/skills/tts/ install if present.
# See plan and docs/ARCHITECTURE.md for details.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_TEMPLATE="$REPO_DIR/launchd/com.claude-code.tts.plist.template"
PLIST_TARGET="$HOME/Library/LaunchAgents/com.claude-code.tts.plist"
OLD_PLIST="$HOME/Library/LaunchAgents/com.kos.claude-tts.plist"
AGENT_LABEL="com.claude-code.tts"
OLD_LABEL="com.kos.claude-tts"
UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"
SERVICE="${DOMAIN}/${AGENT_LABEL}"
STATE_DIR="$HOME/.config/claude-code-tts"
STATE_FILE="$STATE_DIR/enabled"
LEGACY_FLAG="$HOME/.claude/skills/tts/tts-enabled"
SETTINGS_JSON="$HOME/.claude/settings.json"

say()  { printf '\033[0;32m✓\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$1"; }
fail() { printf '\033[0;31m✗\033[0m %s\n' "$1" >&2; exit 1; }

# 1. Prerequisites
command -v python3 >/dev/null 2>&1 || fail "python3 not found"
PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_MAJ="$(echo "$PY_VER" | cut -d. -f1)"
PY_MIN="$(echo "$PY_VER" | cut -d. -f2)"
if [[ "$PY_MAJ" -lt 3 ]] || { [[ "$PY_MAJ" -eq 3 ]] && [[ "$PY_MIN" -lt 10 ]]; }; then
    fail "python3 is $PY_VER; need >= 3.10"
fi
say "python3 $PY_VER"
command -v afplay >/dev/null 2>&1 || fail "afplay missing (ships with macOS)"
command -v jq     >/dev/null 2>&1 || fail "jq not found — install via: brew install jq"
say "afplay, jq found"

# 2. Migrate legacy enabled flag
mkdir -p "$STATE_DIR"
if [[ -f "$LEGACY_FLAG" ]] && [[ ! -f "$STATE_FILE" ]]; then
    cp "$LEGACY_FLAG" "$STATE_FILE"
    say "migrated legacy flag → $STATE_FILE"
elif [[ ! -f "$STATE_FILE" ]]; then
    echo 0 > "$STATE_FILE"
    say "initialized $STATE_FILE = 0"
else
    say "state file exists: $STATE_FILE = $(cat "$STATE_FILE")"
fi

# 3. venv + deps
if [[ ! -d "$REPO_DIR/venv" ]]; then
    python3 -m venv "$REPO_DIR/venv"
    say "venv created"
else
    say "venv already present"
fi
"$REPO_DIR/venv/bin/pip" install --quiet --upgrade pip
"$REPO_DIR/venv/bin/pip" install --quiet --upgrade -r "$REPO_DIR/requirements.txt"
say "edge-tts installed/updated"

# 4. Logs dir
mkdir -p "$REPO_DIR/logs"

# 5. Patch ~/.claude/settings.json (legacy hooks get removed, fresh hooks added)
if [[ -f "$SETTINGS_JSON" ]]; then
    "$REPO_DIR/venv/bin/python" "$REPO_DIR/scripts/patch_settings.py" \
        install --repo-dir "$REPO_DIR" --settings "$SETTINGS_JSON" --backup
    say "settings.json patched (backup saved)"
else
    "$REPO_DIR/venv/bin/python" "$REPO_DIR/scripts/patch_settings.py" \
        install --repo-dir "$REPO_DIR" --settings "$SETTINGS_JSON"
    say "settings.json created"
fi

# 6. Render launchd plist (preserve user-set EnvironmentVariables across re-install)
#
# Re-running install.sh (directly or via `tts update`) used to stomp the whole
# plist, wiping knobs that `tts provider|voice|rate|pitch|openai-*|speed|tldr`
# had written to EnvironmentVariables. Users had to reconfigure everything
# after each update. Now: capture existing EnvironmentVariables, render the
# fresh template, then overlay captured values on top.
#
# PYTHONPATH and PATH are re-rendered from the template because they're
# derived from $REPO_DIR — overlaying a stale value would break the server
# if the repo had been moved.
mkdir -p "$HOME/Library/LaunchAgents"

preserved_env=""
if [[ -f "$PLIST_TARGET" ]]; then
    # jq -r with tab separator is safe here: none of our env values contain
    # tabs (voices, rates, keys are all simple strings). Falls through to
    # empty on malformed plist — template defaults then apply.
    preserved_env=$(
        plutil -convert json -o - "$PLIST_TARGET" 2>/dev/null \
            | jq -r '(.EnvironmentVariables // {}) | to_entries[]
                     | select(.key | IN("PYTHONPATH","PATH") | not)
                     | "\(.key)\t\(.value)"' \
            2>/dev/null || true
    )
fi

sed "s|__REPO_DIR__|$REPO_DIR|g" "$PLIST_TEMPLATE" > "$PLIST_TARGET"

if [[ -n "$preserved_env" ]]; then
    overlaid=0
    while IFS=$'\t' read -r env_key env_val; do
        [[ -z "$env_key" ]] && continue
        if /usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:$env_key $env_val" "$PLIST_TARGET" 2>/dev/null; then
            overlaid=$((overlaid + 1))
        elif /usr/libexec/PlistBuddy -c "Add :EnvironmentVariables:$env_key string $env_val" "$PLIST_TARGET" 2>/dev/null; then
            overlaid=$((overlaid + 1))
        else
            warn "could not preserve $env_key — left at template default"
        fi
    done <<< "$preserved_env"
    say "plist: $PLIST_TARGET (preserved $overlaid user-set env var(s))"
else
    say "plist: $PLIST_TARGET"
fi

# 6b. Validate OPENAI_TTS_MODEL ↔ OPENAI_TTS_VOICE pair against OpenAI's per-model
# allow-list (kept in sync with _openai_voices_for_model in bin/tts — update
# both if OpenAI changes its voice lineup). We only warn: edge-tts users don't
# care, and a warning surfaces the problem without blocking re-install.
openai_model=$(
    /usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:OPENAI_TTS_MODEL" "$PLIST_TARGET" 2>/dev/null \
    || echo "gpt-4o-mini-tts"
)
openai_voice=$(
    /usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:OPENAI_TTS_VOICE" "$PLIST_TARGET" 2>/dev/null \
    || echo "nova"
)
case "$openai_model" in
    tts-1|tts-1-hd)    openai_allowed="alloy ash coral echo fable nova onyx sage shimmer" ;;
    gpt-4o-mini-tts)   openai_allowed="alloy ash ballad coral echo fable marin nova onyx sage shimmer verse cedar" ;;
    *)                 openai_allowed="" ;;
esac
if [[ -z "$openai_allowed" ]]; then
    warn "OPENAI_TTS_MODEL=$openai_model is not a recognized model — check: tts openai-voices"
else
    openai_voice_ok=0
    for v in $openai_allowed; do
        [[ "$v" == "$openai_voice" ]] && openai_voice_ok=1 && break
    done
    if [[ "$openai_voice_ok" -eq 0 ]]; then
        warn "OPENAI_TTS_VOICE=$openai_voice is NOT supported by OPENAI_TTS_MODEL=$openai_model"
        warn "supported for $openai_model: $openai_allowed"
        warn "fix: tts openai-voice <name>  OR  tts openai-model <name>  (see: tts openai-voices)"
    else
        say "openai pair OK: model=$openai_model voice=$openai_voice"
    fi
fi

# 7. Remove legacy launchd agent (if any)
if launchctl print "${DOMAIN}/${OLD_LABEL}" >/dev/null 2>&1; then
    launchctl bootout "$DOMAIN" "$OLD_PLIST" 2>/dev/null || true
    say "removed legacy agent ${OLD_LABEL}"
fi
rm -f "$OLD_PLIST"

# 8. (Re)load new agent
if launchctl print "$SERVICE" >/dev/null 2>&1; then
    launchctl bootout "$DOMAIN" "$PLIST_TARGET" 2>/dev/null || true
fi
launchctl bootstrap "$DOMAIN" "$PLIST_TARGET"
launchctl kickstart -k "$SERVICE" >/dev/null 2>&1 || true
say "launchd agent loaded: $AGENT_LABEL"

# 9. Executable + symlink
chmod +x "$REPO_DIR/bin/tts" "$REPO_DIR/hooks/tts-stop.sh" "$REPO_DIR/hooks/tts-status.sh" \
         "$REPO_DIR/install.sh" "$REPO_DIR/uninstall.sh" 2>/dev/null || true
mkdir -p "$HOME/.local/bin"
ln -sfn "$REPO_DIR/bin/tts" "$HOME/.local/bin/tts"
say "symlink: ~/.local/bin/tts → $REPO_DIR/bin/tts"

# 10. Wait for health
ok=0
for _ in 1 2 3 4 5 6 7 8; do
    if curl -sf --max-time 1 http://127.0.0.1:7852/health >/dev/null 2>&1; then
        ok=1; break
    fi
    sleep 0.5
done
if [[ "$ok" -eq 1 ]]; then
    say "server healthy on http://127.0.0.1:7852"
else
    warn "server not responding yet — tail $REPO_DIR/logs/server.log"
fi

case ":$PATH:" in
    *:"$HOME/.local/bin":*) ;;
    *) warn "~/.local/bin is not on PATH — add: fish_add_path ~/.local/bin" ;;
esac

echo
echo "Next:"
echo "  tts on        # enable TTS"
echo "  tts test      # play a test phrase"
echo "  tts status    # show state"
echo "  tts voices    # list ru/en voices"
