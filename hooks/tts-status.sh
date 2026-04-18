#!/bin/bash
# Claude Code SessionStart hook: print TTS status line on session start.
TTS_FLAG="$HOME/.config/claude-code-tts/enabled"
TTS_PORT="${TTS_PORT:-7852}"

enabled="no"
[ -f "$TTS_FLAG" ] && [ "$(cat "$TTS_FLAG")" = "1" ] && enabled="yes"

if [ "$enabled" = "yes" ]; then
    if curl -sf --max-time 1 http://127.0.0.1:${TTS_PORT}/health >/dev/null 2>&1; then
        echo "🔊 TTS: enabled, server running"
    else
        echo "⚠️ TTS: enabled, but server is not running. Start with: tts on"
    fi
fi
