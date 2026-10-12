#!/bin/bash
# Claude Code SessionStart hook: print TTS status line on session start.
# Server up == TTS on; when it is off the hook stays silent.
TTS_PORT="${TTS_PORT:-7852}"

if curl -sf --max-time 1 http://127.0.0.1:${TTS_PORT}/health >/dev/null 2>&1; then
    echo "🔊 TTS: on"
fi
