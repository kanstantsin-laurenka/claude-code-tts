# Architecture

## Data flow

```
Claude Code assistant message complete
    │
    ▼
~/.claude/settings.json  Stop hook
    → bash <repo>/hooks/tts-stop.sh
    │ stdin JSON: {"last_assistant_message": "...", ...}
    ▼
hooks/tts-stop.sh  (20 lines)
    1. curl /health on 127.0.0.1:7852 (server down == TTS off → exit)
    2. extract .last_assistant_message via jq
    3. curl -X POST /speak {"text": ...}  (backgrounded, non-blocking)
    │ HTTP
    ▼
src/claude_code_tts/server.py  (launchd agent com.claude-code.tts)
    1. clean_for_tts(text)  — strip markdown/code
    2. if /tmp/nl-recording.lock exists → return 423 (skip playback)
    3. provider → temp MP3   (edge-tts | openai | google | yandex, chosen by TTS_PROVIDER)
    4. SIGTERM previous afplay → start new afplay
    │
    ▼
macOS audio output
```

When `TTS_PROVIDER=google`, step 3 goes through `GoogleWithFallback`:

```
providers/google_fallback.py  GoogleWithFallback.synthesize(text)
    1. no key or > 5000 bytes?      → edge-tts (not counted)
    2. "exhausted" flag set?        → edge-tts (sticky until the Pacific month rolls over)
    3. usage.reserve(len(text))     → None (would exceed limit): set flag, notify, edge-tts
    4. warning tier reached?        → notify.send + append a spoken phrase to the text
    5. providers/google.py → Google TTS REST  (any failure → same request on edge-tts)
```

`usage.py` only counts (file `~/.config/claude-code-tts/google-usage.json`, thread-safe, re-read on every call); `notify.py` only runs `osascript`; all alert and fallback policy lives in the wrapper. See [CONFIGURATION.md](CONFIGURATION.md#google-knobs) for the numbers.

SessionStart hook (`hooks/tts-status.sh`) runs once per Claude Code session and prints one line showing whether the server is healthy.

## Why a Stop hook and not a skill

Claude Code skills (`~/.claude/skills/<name>/SKILL.md`) are on-demand slash-commands invoked by either the user (`/<name>`) or the model. They are **not** triggered automatically by session lifecycle events.

Stop hooks **are** — Claude Code executes them every time the assistant stops producing text, and passes the full last assistant message on stdin. This is the only mechanism that gives us automatic playback of every response without any per-turn user action.

## Why an HTTP server instead of direct invocation

An alternative design would be for the hook to invoke `edge-tts` + `afplay` directly. The server exists because:

- **Overlap prevention.** Two Stop events can fire close together (rare but possible). The server maintains a single-playback invariant by SIGTERMing the previous `afplay` before starting a new one.
- **Playback interruption control.** `/stop` lets `tts off` and the lock-file-check stop what's currently playing.
- **Startup cost amortization.** Python + edge-tts import is ~0.3s cold. A persistent server pays this cost once at login (launchd) instead of on every response.
- **State inspection.** `/health` gives an externally-queryable status; `tts status` and the SessionStart hook use it.

## Lock-file coordination

Hammerspoon in the TTS2 (speech-to-text) project writes `/tmp/nl-recording.lock` while the microphone is recording. Our server checks for this file **twice** in the `/speak` path:

1. Before calling edge-tts — fast skip.
2. After synthesis, before starting afplay — catches the race where the user started recording during the ~1s synthesis window.

Without this, Claude Code's voice response would play into the microphone and end up transcribed back into the next prompt.

## File locations after install

| Path | Role | Managed by |
|---|---|---|
| `<repo>/` | Source, CLI, scripts | git |
| `<repo>/venv/` | Python venv with the runtime deps from `uv.lock` (uv; without uv, pip installs the ranges from `pyproject.toml`) | install.sh / scripts/dev-setup.sh |
| `<repo>/logs/server.log` | launchd stdout+stderr | launchd |
| `~/.config/claude-code-tts/google-usage.json` | Google monthly char counter | server (`usage.py`) |
| `~/.claude/settings.json` | Hook registration | patch_settings.py |
| `~/Library/LaunchAgents/com.claude-code.tts.plist` | launchd agent | install.sh |
| `~/.local/bin/tts` | Symlink to `<repo>/bin/tts` | install.sh |

## Hook idempotency

`scripts/patch_settings.py` treats a hook as "ours" if its `command` string contains the current repo path **or** the legacy `/.claude/skills/tts/`. On install, any matching hook is removed first and then fresh ones are added. This makes re-install, migration, and repo relocation safe.
