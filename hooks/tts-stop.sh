#!/bin/bash
# Claude Code Stop hook: send last assistant message to local TTS server.
# Non-blocking (curl backgrounded); silent on failure — Claude Code must never
# be stalled by TTS. See docs/ARCHITECTURE.md for the full flow.
TTS_PORT="${TTS_PORT:-7852}"

# Server up == TTS on (`tts off` stops it); a down server means stay silent.
curl -sf --max-time 1 http://127.0.0.1:${TTS_PORT}/health >/dev/null 2>&1 || exit 0

TEXT=$(jq -r '.last_assistant_message // empty' 2>/dev/null)
[ -z "$TEXT" ] && exit 0

curl -sf --max-time 2 -X POST http://127.0.0.1:${TTS_PORT}/speak \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg t "$TEXT" '{text: $t}')" >/dev/null 2>&1 &
