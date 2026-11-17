# claude-code-tts

[![test](https://github.com/kanstantsin-laurenka/claude-code-tts/actions/workflows/test.yml/badge.svg)](https://github.com/kanstantsin-laurenka/claude-code-tts/actions/workflows/test.yml)

Speak Claude Code's responses aloud on macOS using Microsoft's free neural voices via [edge-tts](https://github.com/rany2/edge-tts) — no API key needed. Optional providers: OpenAI TTS (paid), Google Chirp 3 HD (free tier of 1M chars/month, guarded by a local counter that falls back to edge-tts) and Yandex SpeechKit (paid, Russian voices such as `masha`).

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
- Python 3.11+ — or [uv](https://docs.astral.sh/uv/) (`brew install uv`), which `install.sh` then uses to fetch the right Python itself
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

If a legacy install from `~/.claude/skills/tts/` exists, it is migrated automatically: on/off state, settings.json hook paths, and the launchd agent are all replaced cleanly.

## CLI

```
tts on              Start the server; stays on across reboots
tts off             Stop the server and playback; stays off across reboots
tts toggle          Toggle on/off
tts status          on/off state + server health + lock state + launchd state
tts test [text]     Synthesize and play a phrase
tts restart         Kick the launchd agent (requires TTS on)
tts logs            tail -f server log
tts voice <name>    Switch voice (e.g. ru-RU-DmitryNeural, en-US-AriaNeural)
tts rate <val>      Set speech rate (e.g. +15%, -10%)
tts pitch <val>     Set pitch (e.g. +50Hz, -20Hz)
tts provider <name> Switch provider: edge-tts | openai | google | yandex
tts google-key      Store the Google API key in Keychain (hidden prompt)
tts google-key-clear  Remove the Google API key
tts google-voice <name>  Set Chirp 3 HD voice (e.g. ru-RU-Chirp3-HD-Aoede)
tts google-speed <val>   Set Google speaking rate (0.25–2.0)
tts yandex-key      Store the Yandex Cloud API key in Keychain (hidden prompt)
tts yandex-key-clear  Remove the Yandex API key
tts yandex-voice <name> [role]  Set Yandex voice and role (e.g. masha friendly)
tts yandex-voices   List Yandex voices and their roles
tts yandex-speed <val>   Set Yandex speed (0.1–3.0)
tts yandex-pitch <Hz>    Shift Yandex pitch (-1000..1000 Hz, 0 = natural)
tts yandex-region <ru|kz>  Yandex Cloud installation that issued the key
tts tldr <on|off>   Speak only TL;DR block, silent if absent (default: on)
tts reset           Restore voice/rate/pitch to defaults (single restart)
tts config          Print current env values from the plist
tts voices          List ru/en voices from edge-tts
tts update          git pull --rebase + install.sh (upgrade from remote)
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
| `TTS_TLDR_ONLY` | `1` | Speak only the trailing `TL;DR` block; silent if absent. `tts tldr off` to restore full-message playback |
| `TTS_PROVIDER` | `edge-tts` | `edge-tts`, `openai`, `google` or `yandex` |
| `GOOGLE_TTS_VOICE` | `ru-RU-Chirp3-HD-Aoede` | Google Chirp 3 HD voice |
| `GOOGLE_TTS_SPEED` | `1.0` | Google speaking rate, 0.25–2.0 |
| `GOOGLE_TTS_MONTHLY_CHAR_LIMIT` | `990000` | Local monthly cap; above it the server uses edge-tts until the next (Pacific) month |
| `GOOGLE_TTS_USAGE_FILE` | `~/.config/claude-code-tts/google-usage.json` | Monthly character counter |
| `YANDEX_TTS_VOICE` | `masha` | Yandex SpeechKit voice |
| `YANDEX_TTS_ROLE` | (unset) | Voice role (`good`, `friendly`, …); unset = the voice's default |
| `YANDEX_TTS_SPEED` | `1.0` | Yandex speed, 0.1–3.0 |
| `YANDEX_TTS_PITCH` | `0` | Yandex pitch shift in Hz, −1000…1000 |
| `YANDEX_TTS_REGION` | `ru` | `ru` (console.yandex.cloud) or `kz` (kz.console.yandex.cloud) |
| `TTS_MAX_CHARS` | `1500` | Truncate long responses before synthesis |
| `TTS_PORT` | `7852` | Local HTTP server port |
| `TTS_LOCK_FILE` | `/tmp/nl-recording.lock` | If this file exists, suppress playback |
| `TTS_RESPECT_LOCK` | `1` | Set to `0` to ignore the lock |

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for details and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full data flow.

## Upgrade

```bash
tts update       # git pull --rebase, then install.sh
```

> ⚠️ **Heads-up when upgrading to the TL;DR-only release:** by default the server now speaks only the `TL;DR` block at the end of an assistant message and stays silent when that block is missing. If you prefer the previous "speak the whole message" behavior, run `tts tldr off` once after upgrading.

## Development

Dependencies are managed with [uv](https://docs.astral.sh/uv/): `pyproject.toml` declares them, `uv.lock` pins exact versions, and `.python-version` sets the dev Python (3.14).

```bash
brew install uv
scripts/dev-setup.sh       # venv/ synced from uv.lock (+ dev group) and the pre-push hook
venv/bin/pytest tests/ -v
```

After changing dependencies in `pyproject.toml`, refresh the lock and the venv:

```bash
uv lock
scripts/dev-setup.sh
```

GitHub Actions runs `pytest` on every push across Python 3.11 / 3.12 / 3.13 / 3.14 — see [`.github/workflows/test.yml`](.github/workflows/test.yml).

### Dependency updates

Dependabot ([`.github/dependabot.yml`](.github/dependabot.yml)) proposes updates to `uv.lock` daily, after a 7-day cooldown. Once CI is green, the `automerge` job squash-merges minor/patch bumps. These stay open for a manual merge:

- major bumps;
- updates to a package whose maintainers changed;
- `edge-tts` — it skips the cooldown so Microsoft-side fixes arrive fast, and in exchange a human merges it.

Merged updates reach an installed copy on the next `tts update`.

## License

[MIT](LICENSE). Note that the `edge-tts` dependency is licensed separately (LGPL-3.0).

## Status

Personal tool, happy to accept issues.
