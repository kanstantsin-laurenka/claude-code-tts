# claude-code-tts

Speak Claude Code's responses aloud on macOS using Microsoft's free neural voices via [edge-tts](https://github.com/rany2/edge-tts). Zero cloud keys, zero paid APIs.

```
Claude Code answers
    ↓   (Stop hook in ~/.claude/settings.json)
hooks/tts-stop.sh
    ↓   (HTTP POST {"text": ...})
127.0.0.1:7852  ←  local Python server (launchd-managed)
    ↓
clean markdown → edge-tts → afplay
```

## Requirements

- macOS
- Python 3.10+
- [Homebrew](https://brew.sh) for installing `jq`
- [Claude Code](https://claude.ai/code) CLI

## Install

```bash
git clone git@github.com:<user>/claude-code-tts.git ~/dev/claude-code-tts
cd ~/dev/claude-code-tts
./install.sh
tts on         # enable
tts test       # play a test phrase
```

`install.sh` is idempotent: it creates a Python venv, installs `edge-tts`, renders a launchd plist, adds `Stop` and `SessionStart` hooks to `~/.claude/settings.json` (backing it up), and symlinks `bin/tts` into `~/.local/bin/`.

If a legacy install from `~/.claude/skills/tts/` exists, it is migrated automatically: enabled-flag, settings.json hook paths, and the launchd agent are all replaced cleanly.

## CLI

```
tts on              Enable TTS and (re)start the server
tts off             Disable and stop current playback
tts toggle          Toggle on/off
tts status          enabled flag + server health + lock state + launchd state
tts test [text]     Synthesize and play a phrase
tts restart         Kick the launchd agent
tts logs            tail -f server log
tts voice <name>    Switch voice (e.g. ru-RU-DmitryNeural, en-US-AriaNeural)
tts rate <val>      Set speech rate (e.g. +15%, -10%)
tts pitch <val>     Set pitch (e.g. +50Hz, -20Hz)
tts voices          List ru/en voices from edge-tts
tts install         Re-run installer
tts uninstall       Remove the installation (add --purge to wipe state/venv)
```

## Uninstall

```bash
./uninstall.sh             # removes launchd, hooks, CLI symlink; keeps state + venv
./uninstall.sh --purge     # also removes ~/.config/claude-code-tts, venv, logs
```

The repo directory itself is never deleted — you remove it yourself.

## Configuration

All runtime parameters come from environment variables set in the launchd plist. Use `tts voice/rate/pitch` to edit safely; for anything else edit `~/Library/LaunchAgents/com.claude-code.tts.plist` and run `tts restart`.

| Variable | Default | Purpose |
|---|---|---|
| `TTS_VOICE` | `ru-RU-SvetlanaNeural` | edge-tts voice id |
| `TTS_RATE` | `+0%` | Speech rate delta |
| `TTS_PITCH` | `+0Hz` | Pitch delta |
| `TTS_MAX_CHARS` | `1500` | Truncate long responses before synthesis |
| `TTS_PORT` | `7852` | Local HTTP server port |
| `TTS_LOCK_FILE` | `/tmp/nl-recording.lock` | If this file exists, suppress playback |
| `TTS_RESPECT_LOCK` | `1` | Set to `0` to ignore the lock |
| `TTS_STATE_FILE` | `~/.config/claude-code-tts/enabled` | Enabled-flag path (advisory) |

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for details and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full data flow.

## Development

```bash
venv/bin/pip install -r requirements-dev.txt
venv/bin/pytest tests/ -v
```

## Status

Personal tool, happy to accept issues.
