# Configuration

All knobs live as environment variables in the launchd plist at `~/Library/LaunchAgents/com.claude-code.tts.plist`. The `tts` CLI edits the plist via `PlistBuddy` and restarts the launchd agent so each change takes effect immediately. Everything else requires a manual edit + `tts restart`.

## Providers

The server supports four synthesis backends. Exactly one is active at a time; switch with `tts provider`.

| Provider | Default | Cost | Needs network | Needs key |
|---|---|---|---|---|
| `edge-tts` | yes | free | yes (Microsoft endpoint) | no |
| `openai` | no | paid | yes (OpenAI API) | yes (macOS Keychain) |
| `google` | no | free up to 1M chars/month, then paid | yes (Google Cloud TTS) | yes (macOS Keychain) |
| `yandex` | no | paid, ~650 ₽ / 1M chars, no free tier | yes (Yandex SpeechKit) | yes (macOS Keychain) |

```bash
tts provider edge-tts    # free Microsoft neural voices (default)
tts provider openai      # OpenAI TTS API (requires: tts openai-key)
tts provider google      # Google Chirp 3 HD (requires: tts google-key)
tts provider yandex      # Yandex SpeechKit v3 (requires: tts yandex-key)
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

### edge-tts outages

edge-tts talks to an unofficial Microsoft endpoint. When Microsoft changes it, requests start failing until a new edge-tts release catches up. Every edge-tts failure is logged with the installed version and a cause (`tts logs`):

```
edge-tts 7.3.1 error: Microsoft rejected the request (HTTP 403): …
edge-tts 7.3.1 error: network problem (TimeoutError): …
edge-tts 7.3.1 error: invalid voice/rate/pitch (ValueError): …
```

Only the first kind (Microsoft rejected the request, or answered with something edge-tts did not expect) shows a macOS notification, at most once every 6 hours. The server compares the installed edge-tts with the newest on PyPI:

| Notification ends with | Meaning | What to do |
|---|---|---|
| «Доступна версия X — выполните tts update» | a fixed edge-tts is already released | `tts update` |
| «Новой версии edge-tts пока нет — сбой на стороне Microsoft» | no fix yet | wait; switch provider meanwhile (`tts provider …`) if needed |
| «Проверьте обновления: tts update» | PyPI could not be reached | `tts update`, or `tts doctor` later |

`tts doctor` shows the same comparison any time («edge-tts X installed, Y on PyPI»). New edge-tts releases reach `main` through Dependabot without the usual 7-day cooldown and are merged by hand (README → Dependency updates).

## openai knobs

| Command | Effect | Env var |
|---|---|---|
| `tts openai-key` | prompt for and store the API key in Keychain | — |
| `tts openai-key-clear` | remove stored key from Keychain | — |
| `tts openai-voice <name>` | set voice | `OPENAI_TTS_VOICE` |
| `tts openai-model <name>` | set model (`tts-1` / `tts-1-hd` / `gpt-4o-mini-tts`) | `OPENAI_TTS_MODEL` |
| `tts speed <value>` | set playback speed (0.25–4.0) | `OPENAI_TTS_SPEED` |
| `tts openai-voices` | list OpenAI voices | — |

Defaults: model `gpt-4o-mini-tts`, voice `nova`, speed `1.0`.

**API key:** stored in the macOS Keychain under service `claude-code-tts`, account `openai`. The server reads it at launch time by invoking `security find-generic-password`; the key never appears in the plist, logs, or `/health` output. Running `tts openai-key` when a key already exists asks `key already stored, replace? [y/N]` first. The key is typed at a hidden prompt shown by `security` itself (asked twice), so it never appears in `ps`; afterwards its shape is checked (`sk-…`, 40+ chars) and a malformed key only produces a warning.

**Voices (11):** `alloy`, `ash`, `ballad`, `coral`, `echo`, `fable`, `nova`, `onyx`, `sage`, `shimmer`, `verse`.

**Pricing (as of April 2026):**
- `tts-1` — $15 / 1M characters, low latency
- `tts-1-hd` — $30 / 1M characters, higher quality, higher latency
- `gpt-4o-mini-tts` — $0.60 / 1M input tokens + $12 / 1M audio output tokens (~$0.015/min), newest, supports style instructions

Authoritative pricing: [OpenAI API pricing](https://openai.com/api/pricing/).

**`rate`/`pitch` are edge-tts-only; `speed` is openai-only; `google-speed` is google-only; `yandex-speed` is yandex-only.** The CLI does not prevent you from setting a knob for the inactive provider — it just won't have any effect until you switch to that provider.

**Error behavior:** if an OpenAI call fails (auth, rate limit, no network, no key), the server returns HTTP 502 to the Stop hook, which swallows it silently. The `openai` and `yandex` providers have **no** automatic fallback to edge-tts (the `google` provider does — see below).

### google knobs

Google Cloud Text-to-Speech with Chirp 3 HD voices. The free tier is 1,000,000 characters per month; going over is billed. Google Cloud spend caps do not support Text-to-Speech, so a **local monthly character counter** is the guard: it warns you as usage grows and switches to edge-tts before the limit can be exceeded.

| Command | Effect | Env var |
|---|---|---|
| `tts google-key` | prompt for and store the API key in Keychain | — |
| `tts google-key-clear` | remove stored key from Keychain | — |
| `tts google-voice <name>` | set voice (`ru-RU-Chirp3-HD-Aoede`, `en-US-Chirp3-HD-Puck`, …) | `GOOGLE_TTS_VOICE` |
| `tts google-speed <value>` | set speaking rate (0.25–2.0) | `GOOGLE_TTS_SPEED` |
| — | monthly limit (see below) | `GOOGLE_TTS_MONTHLY_CHAR_LIMIT` |
| — | usage counter file | `GOOGLE_TTS_USAGE_FILE` |

Defaults: voice `ru-RU-Chirp3-HD-Aoede`, speed `1.0`, limit `990000`, usage file `~/.config/claude-code-tts/google-usage.json`. Chirp 3 HD has no pitch control. The language code is derived from the voice name (`ru-RU-…` → `ru-RU`).

**API key:** Keychain service `claude-code-tts`, account `google`; same prompt flow as `tts openai-key`. Expected shape: `AIza…`, about 39 chars (the check accepts 35–45). Send it as the `X-Goog-Api-Key` header only; it is never put in a URL, log line, or `/health`.

**What the numbers mean**

| Term | Meaning |
|---|---|
| Free tier | Google's 1,000,000 chars/month. Google's number; our code never changes it. |
| Used | Characters this app sent to Google in the current month: the text after cleanup/TL;DR, plus any appended warning phrase. Counted *before* the API call. Refunded if the call fails in a way Google does not bill (connection error, HTTP 4xx such as a bad key or 429); **not** refunded for 5xx, read timeouts or bad responses, which may have been billed. |
| Limit / switch threshold | `GOOGLE_TTS_MONTHLY_CHAR_LIMIT`, default **990,000**. The 10,000-char margin absorbs drift between our count and Google's accounting. |
| Warning percentages | Percent of the *limit* (990,000), not of 1,000,000. |
| Month | Calendar month in America/Los_Angeles (Pacific Time); the counter resets when that month changes. |

| Event | Trigger (% of 990k) | Used at trigger | Remaining to 990k | Remaining to Google's 1M |
|---|---|---|---|---|
| Warning 1 (once/month) | ≥ 80% | 792,000 | 198,000 | 208,000 |
| Warning 2 (once/month) | ≥ 90% | 891,000 | 99,000 | 109,000 |
| Critical (every message) | ≥ 98% | 970,200 | 19,800 | 29,800 |
| Switch to edge-tts | used + len(text) > 990,000 | ≤ 990,000 | 0 | 10,000 |

Each warning is a macOS notification plus a spoken phrase appended to the message (Russian text). The spoken "remaining" figure is characters left until the *switch*, rounded (nearest 10 under 1,000; nearest 100 under 10,000; otherwise nearest 1,000).

**Switching is sticky.** The first request that does not fit under the limit sets an `exhausted` flag; every later request that month goes to edge-tts (no flip-flopping between voices) until the Pacific month rolls over. One notification and one spoken notice announce the switch.

**Fallback to edge-tts** (uses `TTS_VOICE`/`TTS_RATE`/`TTS_PITCH`) also happens, on the same request, when: Google fails for any reason (network, 4xx/5xx, 429, timeout, bad response); the Google key is missing; or the text is over 5000 UTF-8 bytes (Cyrillic is 2 bytes per char). Missing key and oversize text are detected before counting, so they cost no quota. Failure-based fallbacks do **not** show a notification — you hear the voice change. `/health` (and `tts status`) show `fallback_count` and `last_fallback` (reason + time; kept in memory, reset on restart).

**Usage file** (schema v1):

```json
{"version": 1, "month": "2026-09", "chars": 123456, "flags": ["warn_80", "warn_90", "exhausted"]}
```

It is re-read on every request, so deleting it resets the counter immediately, no restart needed:

```bash
rm ~/.config/claude-code-tts/google-usage.json
```

A corrupt file is renamed to `google-usage.json.corrupt` and counting restarts from 0. `uninstall.sh --purge` deletes it along with the rest of `~/.config/claude-code-tts`.

**Caveats:** one-shot warnings (80%/90%/switch) are marked as delivered when the request is counted. If that audio is then dropped (microphone lock, or a newer message interrupts it) the spoken phrase is not repeated that month; the notification still appears. The local count can drift from Google's real billing (other clients using the same key, a deleted usage file, no refunds for 5xx/timeouts that may have been billed). The margin absorbs small drift only; keep the $5 budget alert on the Google Cloud billing account as a second line of defense.

### yandex knobs

Yandex SpeechKit, API v3 (REST `utteranceSynthesis`). There is **no free tier** and the Yandex Cloud starter grant is currently not offered to new clients, so every request is billed. Yandex's own "Alice" voice is not available through the API; `masha` is the default.

| Command | Effect | Env var |
|---|---|---|
| `tts yandex-key` | prompt for and store the API key in Keychain | — |
| `tts yandex-key-clear` | remove stored key from Keychain | — |
| `tts yandex-voice <name> [role]` | set voice and role together; no role = the voice's default | `YANDEX_TTS_VOICE`, `YANDEX_TTS_ROLE` |
| `tts yandex-voices` | list voices and the roles each supports | — |
| `tts yandex-speed <value>` | set speed (0.1–3.0) | `YANDEX_TTS_SPEED` |
| `tts yandex-pitch <Hz>` | shift pitch, −1000…1000 Hz (`-100` lower, `+50` higher, `0` natural) | `YANDEX_TTS_PITCH` |
| `tts yandex-region <ru\|kz>` | Yandex Cloud installation that issued the key | `YANDEX_TTS_REGION` |

Defaults: voice `masha`, role unset (masha's default is `good`), speed `1.0`, pitch `0`, region `ru`.

**Region.** Yandex Cloud runs two separate installations with their own accounts, IAM and API hosts: Russia (`console.yandex.cloud`, `tts.api.cloud.yandex.net`) and Kazakhstan (`kz.console.yandex.cloud`, `tts.api.yandexcloud.kz`). An API key works only against the installation that issued it — a mismatch answers 401. If your console URL starts with `kz.`, run `tts yandex-region kz`. The Kazakhstan installation accepted a foreign (Georgian) Visa card for an individual non-resident where the Russian one refused it. Voice and role are set in one command because SpeechKit rejects a role the voice does not have; the CLI validates the pair against the table in `bin/tts` (`_yandex_roles_for_voice`), and `tts doctor` re-checks it after manual plist edits. Listen to the voices in the [SpeechKit demo](https://yandex.cloud/ru/services/speechkit#demo).

**Pricing (as of October 2026):** 0.1626 ₽ per request for the `general` model, counted per started 250-character block (1–250 chars = 1 unit, 251–500 = 2, …) — about 650 ₽ per 1M characters. A typical TL;DR (100–400 chars) costs 0.16–0.33 ₽. Failed requests caused by Yandex server errors are not billed. Authoritative: [SpeechKit pricing](https://aistudio.yandex.ru/docs/ru/speechkit/pricing). Yandex Cloud budgets only send notifications, they do not stop the API — set one up anyway (Billing → Budgets).

**Getting an API key** (one-time, in the [Yandex Cloud console](https://console.yandex.cloud)):

Use the console of your installation (`console.yandex.cloud` or `kz.console.yandex.cloud`) for every step.

1. **Billing account.** Billing → create a billing account and link a card. Its status must be `ACTIVE`; with no active billing account SpeechKit answers every request with an error.
2. **Cloud and folder.** The billing account needs a linked cloud ("Linked clouds" on the billing account page). Use the `default` folder of your cloud or create one (e.g. `claude-code-tts`).
3. **Service account.** In the folder: Identity and Access Management → Service accounts → Create. Name it e.g. `claude-code-tts` and give it the role **`ai.speechkit-tts.user`**. Requests are billed to the folder this account lives in, so no folder ID is configured anywhere.
4. **API key.** Open the service account → Create new key → **API key**. Scope: **`yc.ai.speechkitTts.execute`** (the console requires one). Optionally set an expiry. Copy the secret now — it is shown only once. Yandex does not document the key format (seen prefixes: `AQVN…`, `AQWJ…`).
5. **Store it** in a normal terminal (needs a tty; Claude Code's `!` has none):

   ```bash
   tts yandex-key
   tts yandex-region kz     # only for kz.console.yandex.cloud
   tts provider yandex
   tts test
   ```

An IAM token (`t1.…`) is not a substitute: it expires after 12 hours. `tts doctor` warns when the stored key looks like an IAM token, contains spaces, or is not 30–80 chars long.

**API key storage:** Keychain service `claude-code-tts`, account `yandex`; same prompt flow as `tts openai-key`. Sent only as the `Authorization: Api-Key …` header; never put in a URL, log line, or `/health`.

**Error behavior:** like `openai` — any failure (bad key, inactive billing, unsupported role, network) returns HTTP 502 and the Stop hook stays silent; the reason is in `logs/server.log` (`tts logs`). Text longer than 250 characters is sent with `unsafeMode`, so SpeechKit splits it itself instead of rejecting it.

## Manual knobs

Edit the plist with PlistBuddy, then `tts restart`:

```bash
/usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:TTS_MAX_CHARS 3000" \
    ~/Library/LaunchAgents/com.claude-code.tts.plist
tts restart
```

| Variable | Default | Notes |
|---|---|---|
| `TTS_TLDR_ONLY` | `1` | When `1`, the server speaks only the text after the last `TL;DR` marker. If the message has no marker the server stays silent. Toggle with `tts tldr on/off`. |
| `TTS_MAX_CHARS` | `1500` | Soft limit ~10 s of speech; exceeded text gets a spoken "ответ сокращён" suffix |
| `TTS_PORT` | `7852` | Change only if 7852 is busy on your machine |
| `TTS_LOCK_FILE` | `/tmp/nl-recording.lock` | File whose presence suppresses playback |
| `TTS_RESPECT_LOCK` | `1` | Set to `0` to ignore the lock entirely |
| `PYTHONPATH` | `<repo>/src` | Set by `install.sh`; needed so `python -m claude_code_tts.server` resolves |

## Picking a voice

```bash
tts voices                      # edge-tts: ru/en voices
tts voice ru-RU-DmitryNeural    # edge-tts: male Russian
tts openai-voices               # openai: full list
tts openai-voice nova           # openai: warm female
tts google-voice ru-RU-Chirp3-HD-Aoede   # google: Chirp 3 HD
```

edge-tts full list (hundreds of locales): run `tts voices` without filters, or see [edge-tts voice samples](https://tts.travisvn.com/).

## TL;DR-only mode

> ⚠️ **Breaking change (introduced with `TTS_TLDR_ONLY`):** before this change the server always synthesized the entire assistant reply. Now the default is `TTS_TLDR_ONLY=1`, meaning the server stays silent for any reply that does not contain a TL;DR block. If you want the old "speak everything" behavior, run `tts tldr off` once after updating — the setting is persisted in the launchd plist.

Default behavior: the server speaks **only the TL;DR block** at the end of an assistant message. Anything before the last line matching one of `## TL;DR`, `**TL;DR**`, `TL;DR:`, or bare `TL;DR` (case-insensitive, at line start) is dropped before synthesis. Fenced code blocks are stripped before the marker search, so an example TL;DR inside a `` ``` `` block does not trigger a match. Asymmetric markdown like `*TL;DR*`, `*TL;DR`, or `TL;DR**` is **not** treated as a marker. If no marker is found, the server is silent (HTTP 204) and logs a short preview of the rejected text so you can trace why nothing was spoken.

This keeps playback short — assistant-side workflow is expected to always emit a concise summary block at the end. Toggle off to restore the old "speak the whole message" behavior:

```bash
tts tldr off     # speak the full message
tts tldr on      # speak only TL;DR (default)
```

Running `tts tldr on` when it is already on (or `off` when already off) is a no-op — the CLI prints `tldr-only: already <mode> (no change)` and does not restart the launchd agent, so active playback is never interrupted.

## On / off

"On" means the launchd agent is enabled and the server is running; there is no flag file.

| Command | Effect |
|---|---|
| `tts on` | `launchctl enable` + load + start the server. Survives reboots: the server comes back at login. |
| `tts off` | stops playback, `launchctl disable` + boot out the server. Stays off after reboot until `tts on`, so an off TTS uses no memory. |

Hooks only check `/health`: server up = speak, server down = silent. While TTS is off, config commands (`tts voice`, `tts provider`, `tts reset`, …) just save to the plist and print `saved; applies on next 'tts on'`; `tts test` and `tts restart` refuse with `TTS is off — run: tts on`. A first `install.sh` leaves TTS off; re-running it (or `tts update`) keeps the current state.

## Logs

```bash
tts logs              # tail -f <repo>/logs/server.log
```

launchd redirects stdout and stderr into `logs/server.log`. Expect one line per speak request, plus any errors from edge-tts, OpenAI, or afplay.
