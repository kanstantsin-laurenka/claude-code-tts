# Configuration

All knobs live as environment variables in the launchd plist at `~/Library/LaunchAgents/com.claude-code.tts.plist`. The `tts` CLI edits the plist via `PlistBuddy` and restarts the launchd agent so each change takes effect immediately. Everything else requires a manual edit + `tts restart`.

## Providers

The server supports two synthesis backends. Exactly one is active at a time; switch with `tts provider`.

| Provider | Default | Cost | Needs network | Needs key |
|---|---|---|---|---|
| `edge-tts` | yes | free | yes (Microsoft endpoint) | no |
| `openai` | no | paid | yes (OpenAI API) | yes (macOS Keychain) |

```bash
tts provider edge-tts    # free Microsoft neural voices (default)
tts provider openai      # OpenAI TTS API (requires: tts openai-key)
```

`TTS_PROVIDER` is the underlying env var. Unknown values fall back to `edge-tts` with a log line.

### edge-tts knobs

| Command | Effect | Env var |
|---|---|---|
| `tts voice <name>` | set voice | `TTS_VOICE` |
| `tts rate <value>` | set speech rate (e.g. `+15%`, `-10%`) | `TTS_RATE` |
| `tts pitch <value>` | set pitch (e.g. `+50Hz`, `-20Hz`) | `TTS_PITCH` |
| `tts voices` | list ru/en voices | — |
| `tts reset` | restore voice/rate/pitch to defaults | — |

Defaults: `ru-RU-SvetlanaNeural`, `+0%`, `+0Hz`.

### openai knobs

| Command | Effect | Env var |
|---|---|---|
| `tts openai-key` | prompt for and store the API key in Keychain | — |
| `tts openai-key-clear` | remove stored key from Keychain | — |
| `tts openai-voice <name>` | set voice | `OPENAI_TTS_VOICE` |
| `tts openai-model <name>` | set model (`tts-1` / `tts-1-hd` / `gpt-4o-mini-tts`) | `OPENAI_TTS_MODEL` |
| `tts speed <value>` | set playback speed (0.25–4.0) | `OPENAI_TTS_SPEED` |
| `tts openai-voices` | list OpenAI voices | — |

Defaults: model `tts-1`, voice `nova`, speed `1.0`.

**API key:** stored in the macOS Keychain under service `claude-code-tts`, account `openai`. The server reads it at launch time by invoking `security find-generic-password`; the key never appears in the plist, logs, or `/health` output. Running `tts openai-key` again overwrites the existing entry.

**Voices (11):** `alloy`, `ash`, `ballad`, `coral`, `echo`, `fable`, `nova`, `onyx`, `sage`, `shimmer`, `verse`.

**Pricing (as of April 2026):**
- `tts-1` — $15 / 1M characters, low latency
- `tts-1-hd` — $30 / 1M characters, higher quality, higher latency
- `gpt-4o-mini-tts` — $0.60 / 1M input tokens + $12 / 1M audio output tokens (~$0.015/min), newest, supports style instructions

Authoritative pricing: [OpenAI API pricing](https://openai.com/api/pricing/).

**`rate`/`pitch` are edge-tts-only; `speed` is openai-only.** The CLI does not prevent you from setting a knob for the inactive provider — it just won't have any effect until you switch to that provider.

**Error behavior:** if an OpenAI call fails (auth, rate limit, no network, no key), the server returns HTTP 502 to the Stop hook, which swallows it silently. There is **no** automatic fallback to edge-tts.

## Manual knobs

Edit the plist with PlistBuddy, then `tts restart`:

```bash
/usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:TTS_MAX_CHARS 3000" \
    ~/Library/LaunchAgents/com.claude-code.tts.plist
tts restart
```

| Variable | Default | Notes |
|---|---|---|
| `TTS_MAX_CHARS` | `1500` | Soft limit ~10 s of speech; exceeded text gets a spoken "ответ сокращён" suffix |
| `TTS_PORT` | `7852` | Change only if 7852 is busy on your machine |
| `TTS_LOCK_FILE` | `/tmp/nl-recording.lock` | File whose presence suppresses playback |
| `TTS_RESPECT_LOCK` | `1` | Set to `0` to ignore the lock entirely |
| `TTS_STATE_FILE` | `~/.config/claude-code-tts/enabled` | Advisory — the `enabled` flag is read by hooks, not by the server |
| `PYTHONPATH` | `<repo>/src` | Set by `install.sh`; needed so `python -m claude_code_tts.server` resolves |

## Picking a voice

```bash
tts voices                      # edge-tts: ru/en voices
tts voice ru-RU-DmitryNeural    # edge-tts: male Russian
tts openai-voices               # openai: full list
tts openai-voice nova           # openai: warm female
```

edge-tts full list (hundreds of locales): run `tts voices` without filters, or see [edge-tts voice samples](https://tts.travisvn.com/).

## Enabled flag

`~/.config/claude-code-tts/enabled` contains either `0` or `1`. Both hooks (`tts-stop.sh`, `tts-status.sh`) read it. `tts on`/`off` write it. The server itself does **not** consult the flag — playback is gated entirely in the hook.

## Logs

```bash
tts logs              # tail -f <repo>/logs/server.log
```

launchd redirects stdout and stderr into `logs/server.log`. Expect one line per speak request, plus any errors from edge-tts, OpenAI, or afplay.
