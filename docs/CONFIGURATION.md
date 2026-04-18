# Configuration

All knobs live as environment variables in the launchd plist at `~/Library/LaunchAgents/com.claude-code.tts.plist`. Three of them — voice, rate, pitch — have dedicated `tts` CLI subcommands that edit the plist via `PlistBuddy` and restart the agent. The rest require a manual edit.

## CLI-managed

| Command | Effect | Validation |
|---|---|---|
| `tts voice <name>` | `TTS_VOICE=<name>` | none (edge-tts accepts any id) |
| `tts rate <value>` | `TTS_RATE=<value>` | must match `^[+-]\d+%$` |
| `tts pitch <value>` | `TTS_PITCH=<value>` | must match `^[+-]\d+Hz$` |
| `tts reset` | Restore voice/rate/pitch to defaults | — |
| `tts config` | Print current env values from the plist | — |

After each change the launchd agent is `bootout`-ed and `bootstrap`-ed, and the CLI waits up to 3 s for `/health` before returning. `tts reset` writes all three keys then reloads once (not three times).

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
tts voices                      # list ru/en voices
tts voice ru-RU-DmitryNeural    # male Russian
tts voice en-US-AriaNeural      # female US English
```

Full list (hundreds of locales): run `tts voices` without filters, or see [edge-tts voice samples](https://tts.travisvn.com/).

## Enabled flag

`~/.config/claude-code-tts/enabled` contains either `0` or `1`. Both hooks (`tts-stop.sh`, `tts-status.sh`) read it. `tts on`/`off` write it. The server itself does **not** consult the flag — playback is gated entirely in the hook.

## Logs

```bash
tts logs              # tail -f <repo>/logs/server.log
```

launchd redirects stdout and stderr into `logs/server.log`. Expect one line per speak request, plus any errors from edge-tts or afplay.
