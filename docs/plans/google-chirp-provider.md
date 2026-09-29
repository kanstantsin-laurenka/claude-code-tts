# Implementation plan: Google Chirp 3 HD provider + monthly quota guard

Status: implemented (see the PR that added this file). Kept as design documentation:
sections 3-4 explain the limit/alert numbers and the locked decisions; the build
sequence and manual-smoke-test notes below are historical.
Original audience: a fresh Claude Code session that had NOT seen the design discussion.

The user communicates in Russian. Reply to them in Russian. Persistent files
(code comments, docs, this plan) are written in English.

This plan file is uncommitted working material. Before opening the PR ask the user whether
to keep it (e.g. as design documentation under docs/) or delete it.

---------------------------------------------------------------------------------------

## 1. Goal

Add a third TTS provider `google` (Google Cloud Text-to-Speech, Chirp 3 HD voices,
free tier 1,000,000 characters/month) next to `edge-tts` (default) and `openai`.
Because the free tier is limited and going over costs money, add a local monthly
character counter that (a) warns the user as usage approaches the limit and
(b) switches to the free edge-tts BEFORE the limit can be exceeded.

Data flow stays: Claude Code Stop hook -> hooks/tts-stop.sh -> POST 127.0.0.1:7852 ->
server.py -> clean_text -> provider -> afplay.

## 2. What is already done outside the code (by the user, do not redo)

- Google Cloud project `claude-code-tts` (project id `claude-code-tts-510115`) exists.
- Billing account `claude-code-tts-billing` is linked; a $5 alert-only budget exists
  (50/90/100%). Spend caps do not support Text-to-Speech, so the local counter is the
  only real guard.
- Cloud Text-to-Speech API is enabled. An API key restricted to that API is created.
- The key is ALREADY stored in the macOS Keychain: service `claude-code-tts`,
  account `google`. The OpenAI key lives under account `openai`.
- The user verified `curl` synthesis with voice `ru-RU-Chirp3-HD-Aoede` works.

NEVER overwrite/delete the real Keychain items `-a google` / `-a openai` while
testing. For any manual Keychain experiment use a throwaway account (e.g.
`-a google-test`) and delete it afterwards.

## 3. Glossary and numbers (the user asked to make these exact)

| Term | Meaning |
|---|---|
| Free tier | Google's 1,000,000 chars/month. Google's number; our code never changes it. |
| Used | Characters this app sent to Google in the current month: the `cleaned` text after clean_text/TL;DR, PLUS any appended warning phrase. Counted BEFORE the API call; NOT refunded on failure. |
| Limit / switch threshold | `GOOGLE_TTS_MONTHLY_CHAR_LIMIT`, default **990,000**. Leaves a 10,000-char margin because our local count can drift from Google's accounting. |
| Warning percentages | Percent of the LIMIT (990,000), NOT of 1,000,000. |
| Month | Calendar month in America/Los_Angeles (Pacific Time). Counter resets at the start of each Pacific month. (Believed to match Google's billing period; verify in Google docs while implementing, see section 12.) |

| Event | Trigger (% of 990k) | Used at trigger | Remaining to 990k | Remaining to Google's 1M |
|---|---|---|---|---|
| Warning 1 (once/month) | >= 80% | 792,000 | 198,000 | 208,000 |
| Warning 2 (once/month) | >= 90% | 891,000 | 99,000 | 109,000 |
| Critical (EVERY message) | >= 98% | 970,200 | 19,800 | 29,800 |
| Switch to edge-tts | used + len(text) > 990,000 | <= 990,000 | 0 | 10,000 |

Use integer math for percentages: `used * 100 >= limit * pct` (no floats).

## 4. Locked decisions (do not re-litigate)

1. Fallback provider is edge-tts built with the existing TTS_VOICE/TTS_RATE/TTS_PITCH.
2. Fallback happens: (a) when the quota pre-check fails; (b) on the SAME request when
   Google fails for ANY reason (network, 4xx, 5xx, 429, timeout, bad response); (c) when
   the Google key is missing; (d) when text > 5000 UTF-8 bytes. Cases (c) and (d) are
   detected BEFORE counting and count nothing. Every fallback is logged and visible in
   /health. No macOS notification for failure-based fallbacks (the user hears the voice change).
3. **Exhaustion is sticky until the month rolls over.** After the first request that does
   not fit under the limit, a persisted `exhausted` flag is set and ALL later requests in
   that month go to edge-tts (no flip-flopping between voices). A new month resets it.
4. Pre-check is per request and atomic: if `used + len(text) > limit` the request goes to
   edge-tts and is not counted. No single request can cross the limit.
5. Notifications AND spoken warning (both):
   - 80%: once per month. 90%: once per month. (If one request jumps past both, set both
     flags and speak only the highest.)
   - >= 98% (and below the switch): after EVERY message.
   - At the moment of switching (first exhaustion): one notification + one spoken notice.
6. Spoken warning = a phrase APPENDED to the message text (single MP3, single afplay,
   keeps the "only one afplay alive" invariant). Its chars are counted (`usage.add`) only
   if it was actually appended. If text + phrase would exceed 5000 bytes, drop the phrase but
   still notify. The switch notice is appended to the text that goes to edge-tts (not counted).
7. Texts (Russian, module constants in google_fallback.py):
   - spoken 80/90: `"Внимание: использовано {pct} процентов лимита Google."`
   - spoken critical: `"Лимит Google почти исчерпан, до переключения на запасной голос осталось около {N} символов."`
     with N = limit - used rounded (under 1000: nearest 10; under 10000: nearest 100; otherwise
     nearest 1000). "Remaining" means remaining until the SWITCH, not until 1M.
   - spoken switch: `"Лимит Google исчерпан, перехожу на запасной голос."`
   - notification title for all: `claude-code-tts`
   - notification messages: 80/90 `"Использовано {pct}% лимита Google TTS"`; critical
     `"Лимит Google TTS почти исчерпан: осталось около {N} символов до переключения на edge-tts"`;
     switch `"Лимит Google TTS исчерпан, перехожу на edge-tts до конца месяца"`.
8. Notifications via `osascript` with message/title passed as argv items (no string
   interpolation into AppleScript), run in a daemon thread, never raise, never block.
9. Key entry for BOTH `tts google-key` and `tts openai-key` (behavior change for openai):
   - If a key already exists: ask `key already stored, replace? [y/N]` first (abort unless y;
     abort if stdin is not a tty).
   - Then `security add-generic-password -U -s claude-code-tts -a <account> -w` with NO value:
     `security` prompts on the tty (hidden, asked twice); the key never appears in argv/ps.
   - Then read it back and check shape; malformed => WARN only (do not delete):
     openai `sk-*` and length >= 40; google `AIza*` and length 35..45 (real keys ~39).
   - If the server is healthy, `_reload_agent`.
10. Counter storage: `~/.config/claude-code-tts/google-usage.json`, override with env
    `GOOGLE_TTS_USAGE_FILE` (tests point it at tmp_path). Re-read the file on EVERY
    operation (so `rm google-usage.json` takes effect immediately, no restart needed).
11. Speed: `GOOGLE_TTS_SPEED` range 0.25-2.0 (official Chirp 3 HD range), clamp + log like
    OpenAIProvider. No pitch (unsupported by Chirp 3 HD). Default voice
    `ru-RU-Chirp3-HD-Aoede`; languageCode = first two dash-separated parts of the voice.
12. No provider registry refactor, no ProviderError subclass hierarchy, no circuit breaker,
    no Notifier class. Keep the diff proportionate.
13. tzdata: add to `requirements-dev.txt` ONLY; code falls back to a fixed UTC-8 offset
    (logged once) if the zone database is missing.
14. `httpx>=0.27` is added to `requirements.txt` explicitly (currently only transitive via openai).
15. Delete dead code `set_openai_key` / `delete_openai_key` in keychain.py after grepping
    that nothing calls them (bin/tts owns all Keychain writes; their "stdin" docstring is wrong).

## 5. Repo rules (from CLAUDE.md files — follow them)

- The worktree ALREADY EXISTS: `/Users/kos/dev/claude-code-tts-google-chirp` on branch
  `feat/google-chirp-provider` (created from main at 2cf95cc). Enter it with
  `EnterWorktree({path: "/Users/kos/dev/claude-code-tts-google-chirp"})` (load the tool via
  ToolSearch first) and work only there. Do not create another worktree. NEVER commit to main.
  One task = one worktree = one branch = one PR. Commit style: `feat(google): ...`.
- A PreToolUse hook blocks file edits while on the main branch; that is expected.
- macOS only; no Linux/Windows shims. But CI runs pytest on Linux (3.10-3.13), so tests must
  not need `security`, `afplay` or `osascript`. Code must be Python 3.10 compatible
  (`timezone.utc`, no `datetime.UTC`, no `tomllib`, no StrEnum).
- ruff line-length 100; run `/verify` (ruff format-check + lint + pytest) before finishing.
- Bash: `set -euo pipefail` style, readable, no clever one-liners.
- Do NOT run `bin/tts install|on`, `install.sh`, or anything that patches
  `~/.claude/settings.json` or registers launchd agents. Never write settings.json directly.
  Do not touch `.claude/settings.local.json`.
- Comments: WHY not WHAT. Every non-trivial module gets a header block (purpose, business
  rules, dependencies). Update every comment made stale by an edit (server.py header lines
  ~1-32, providers/__init__.py docstring, keychain.py header, bin/tts header "Two providers",
  install.sh comments). Use HACK/WARNING/INVARIANT/TODO/PERF tags per the user's global rules.
- The venv: this worktree has no venv. For tests use the main checkout's venv
  (`/Users/kos/dev/claude-code-tts/venv/bin/python -m pytest`) with the worktree as cwd
  (pyproject sets pythonpath=src,scripts), or run `scripts/dev-setup.sh` in the worktree
  (it also sets core.hooksPath) if the user prefers an isolated venv.
- After finishing, the user's global rules require: rate the result 1-10 and improve it
  yourself if < 8; final message format: (1) what was done (2) what is needed from the user
  (3) next steps, plus one improvement suggestion, one Claude Code tip, and a TL;DR block as
  the LAST part.

## 6. Existing code facts you must respect

- `server.py`: module-level env config; `PROVIDER = _make_provider()` frozen at import;
  `_speak()` runs `asyncio.run(PROVIDER.synthesize(cleaned, tmp_path))` per request inside
  ThreadingHTTPServer handler threads (one thread per request, synthesis is NOT serialized);
  ProviderError -> HTTP 502; `PROVIDER.name` used in the "speaking N chars via ..." log;
  `/health` returns `config: PROVIDER.describe()`. `_speak`/`Handler` need NO change.
- `_parse_speed` hard-codes the name "OPENAI_TTS_SPEED" in its log message: add a `name`
  parameter (default keeps current behavior). A bad env value must never crash import
  (launchd would restart-loop): log and use the default.
- `clean_for_tts` truncates at TTS_MAX_CHARS (default 1500) and appends a Russian suffix,
  so the final text can be ~17 chars longer than MAX_CHARS. Count `len(text)` of what is sent.
- `keychain.py`: `server.py` calls `keychain.get_openai_key()` via the MODULE attribute and
  tests monkeypatch it. Keep that call shape for `get_google_key()`.
- Because `asyncio.run` creates a new event loop per request, create
  `httpx.AsyncClient` INSIDE `synthesize()` (`async with`), never in `__init__`.
- Tests: no conftest; fixtures inline; hand-written fakes + `monkeypatch.setattr` (no
  unittest.mock); server tests use `importlib.reload(srv)` with env set first, random free
  port, `_patch_edge`, `_patch_afplay`, and `keychain_mod.get_*_key` monkeypatched. No
  freezegun; inject a clock callable.
- `install.sh` already preserves every `EnvironmentVariables` key generically and the plist
  template declares only a few vars; new `GOOGLE_*` vars need NO plist template or install.sh
  logic change (add at most a one-line comment). `uninstall.sh --purge` already removes
  `~/.config/claude-code-tts` (which contains the usage file) — verify.
- `bin/tts`: `COMMANDS` array drives dispatch/help (`cmd_${name//-/_}`); `_update_env KEY VAL`
  writes the plist via PlistBuddy and reloads the agent; `cmd_status` prints raw /health JSON
  (usage display must be added with jq and `// empty` guards); doctor/config read the plist
  and Keychain directly.

## 7. Files

Create:
- `src/claude_code_tts/usage.py` — `UsageCounter`
- `src/claude_code_tts/notify.py` — `send(title, message)`
- `src/claude_code_tts/providers/google.py` — REST provider (no quota knowledge)
- `src/claude_code_tts/providers/google_fallback.py` — wrapper: quota, alerts, fallback
- `tests/test_usage.py`, `tests/test_google_provider.py`, `tests/test_google_fallback.py`,
  `tests/test_keychain.py`

Modify:
- `src/claude_code_tts/keychain.py`, `src/claude_code_tts/server.py`
- `requirements.txt` (+`httpx>=0.27`), `requirements-dev.txt` (+`tzdata`)
- `bin/tts`, `install.sh` (comment only), `tests/test_server.py`
- `docs/CONFIGURATION.md`, `docs/ARCHITECTURE.md`, `README.md`, `CLAUDE.md` (Layout section),
  `src/claude_code_tts/providers/__init__.py` (docstring)

## 8. Module specs

### 8.1 keychain.py
```python
SERVICE = "claude-code-tts"; ACCOUNT_OPENAI = "openai"; ACCOUNT_GOOGLE = "google"
def get_key(account: str) -> str | None      # today's get_openai_key body, parameterized
def get_openai_key() -> str | None: return get_key(ACCOUNT_OPENAI)
def get_google_key() -> str | None: return get_key(ACCOUNT_GOOGLE)
```
Keep INVARIANT: `capture_output=True` (security echoes the value on error). Header: read-only
module; writes are done by bin/tts via `security` prompting on the tty.

### 8.2 usage.py
```python
DEFAULT_LIMIT = 990_000
WARN_THRESHOLDS = (80, 90)   # once per month
CRITICAL_PCT = 98            # every message

@dataclass(frozen=True)
class Reservation:
    used: int                 # after this reservation
    limit: int
    crossed: tuple[int, ...]  # one-shot thresholds NEWLY crossed by this reservation (flags already persisted)
    @property remaining -> int
    def percent_at_least(self, pct) -> bool   # integer math

class UsageCounter:
    def __init__(self, path: str, limit: int, now: Callable[[], datetime] | None = None)
    def reserve(self, n: int) -> Reservation | None  # None => would exceed limit, NOTHING counted
    def add(self, n: int) -> None                    # unconditional (appended warning phrase)
    def claim_once(self, flag: str) -> bool          # True only the first time this month; persisted
    def flag_set(self, flag: str) -> bool            # e.g. "exhausted" (sticky check)
    def snapshot(self) -> dict                       # month, used, limit, percent, remaining, flags

def month_key(now: datetime) -> str                  # "YYYY-MM" in America/Los_Angeles
def parse_limit(raw: str | None) -> int              # tolerant; never raises
```
- `now` default `lambda: datetime.now(timezone.utc)`; must return tz-aware datetimes.
- One `threading.Lock` around every load-modify-write cycle in every public method.
- Every operation re-reads the file, rolls the month if `state.month != month_key(now())`
  (reset chars to 0 and flags to empty), then acts.
- `reserve`: if `chars + n > limit` -> None. Else add, set flags for ALL thresholds now
  crossed that were not yet set (both 80 and 90 if jumped), persist, return Reservation
  with `crossed` = newly set ones. The 98% "critical" tier is NOT persisted (fires each time).
- Atomic write: `tempfile.mkstemp(dir=parent)` -> write -> `os.replace`; `os.makedirs(parent, exist_ok=True)`.
  Write failure: log + swallow (never break speech; counter may under-count — accepted).
- Missing file: zeros, no log. Corrupt JSON / wrong types / missing keys: log loudly, best-effort
  rename to `<path>.corrupt`, start from zero.
- `_pacific_tz()`: `ZoneInfo("America/Los_Angeles")`; on `ZoneInfoNotFoundError` log once and use
  `timezone(timedelta(hours=-8))` (off by 1h during DST; only affects the month boundary).
- `parse_limit`: strip whitespace/`,`/`_`, `int(float(x))`; empty/invalid/<=0 -> DEFAULT_LIMIT + log.
- File format (schema v1):
  `{"version":1,"month":"2026-09","chars":123456,"flags":["warn_80","warn_90","exhausted"]}`

### 8.3 notify.py
```python
def send(title: str, message: str) -> None
```
Daemon thread running `subprocess.run(["osascript","-e","on run argv","-e",
"display notification (item 1 of argv) with title (item 2 of argv)","-e","end run",
message, title], timeout=5, capture_output=True)` inside `try/except Exception` (log to stderr;
swallows FileNotFoundError on Linux CI). Header note: notifications from a launchd agent may
appear under "Script Editor" and need a one-time allow in System Settings. UNVERIFIED that it
works from the launchd agent — see section 12.

### 8.4 providers/google.py
```python
GOOGLE_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
SPEED_MIN, SPEED_MAX = 0.25, 2.0
MAX_INPUT_BYTES = 5000
class GoogleTTSProvider(TTSProvider):
    name = "google"
    def __init__(self, voice: str, speed: float, api_key: str | None) -> None
    @property has_key -> bool
    def unavailable_reason(self, text: str) -> str | None   # "no API key" | "text > 5000 bytes"
    async def synthesize(self, text: str, out_path: str) -> None
    def describe(self) -> dict    # provider, voice, speed, key_loaded — NEVER the key
```
- Request: POST GOOGLE_URL, header `X-Goog-Api-Key` (never in the URL), JSON
  `{"input":{"text":...},"voice":{"languageCode":"ru-RU","name":voice},
    "audioConfig":{"audioEncoding":"MP3","speakingRate":speed}}`
- `httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0))` created inside `synthesize`.
- Errors -> `ProviderError` (chain with `from e`): missing key; >5000 bytes; non-200
  (`f"Google TTS HTTP {status}: {body[:200]}"`); `httpx.HTTPError`; bad JSON/base64/missing
  `audioContent`. Never put the key in messages. Write the file only after a successful decode.

### 8.5 providers/google_fallback.py
```python
class GoogleWithFallback(TTSProvider):
    name = "google"
    def __init__(self, google, fallback: TTSProvider, usage: UsageCounter,
                 notify: Callable[[str, str], None]) -> None
    async def synthesize(self, text, out_path) -> None
    def describe(self) -> dict   # google.describe() + "fallback": fallback.name, "usage": usage.snapshot(),
                                 #   "fallback_count": N, "last_fallback": {"at": iso, "reason": str} | None
def round_remaining(n: int) -> int
```
All notification/warning policy lives ONLY here (usage.py knows counts, notify.py knows osascript).
`fallback_count`/`last_fallback` are in memory (reset on restart), guarded by a small lock.
Wrap every `notify` call so it can never raise.

Request flow (sticky exhaustion):
```
synthesize(text, out_path):
    why = google.unavailable_reason(text)                  # no key / >5000 bytes: NOT counted
    if why: return await _edge(text, out_path, why)

    if usage.flag_set("exhausted"):                        # sticky until month rollover
        return await _edge(text, out_path, "monthly limit (exhausted)")

    r = usage.reserve(len(text))
    if r is None:                                          # does not fit under the limit
        if usage.claim_once("exhausted"):
            notify(TITLE, SWITCH_NOTIFICATION)
            text = text + " " + SWITCH_PHRASE              # spoken by edge, not counted
        return await _edge(text, out_path, "monthly limit")

    phrase, note = "", None
    if r.percent_at_least(98):
        phrase, note = CRITICAL_PHRASE(N), CRITICAL_NOTE(N)   # every message; N = round_remaining(r.remaining)
    elif r.crossed:
        pct = max(r.crossed); phrase, note = WARN_PHRASE(pct), WARN_NOTE(pct)   # once (flags persisted in reserve)
    if note: notify(TITLE, note)
    spoken = text
    if phrase and utf8_len(text + " " + phrase) <= MAX_INPUT_BYTES:
        spoken = text + " " + phrase
        usage.add(len(phrase))                             # count only what we actually send
    try:
        await google.synthesize(spoken, out_path)
    except Exception as e:                                 # any Google failure -> same request to edge
        return await _edge(spoken, out_path, str(e))       # counted chars are NOT refunded

_edge(text, out_path, reason):
    log(f"google -> edge-tts fallback: {reason}"); update fallback_count/last_fallback
    await fallback.synthesize(text, out_path)              # edge ProviderError propagates -> server 502
```
Cosmetic accepted quirk: server logs "speaking N chars via google" even when edge served the
request; the wrapper's own fallback log line precedes it and /health.last_fallback shows why.

### 8.6 server.py
- New constants: `GOOGLE_VOICE` (GOOGLE_TTS_VOICE, default `ru-RU-Chirp3-HD-Aoede`),
  `GOOGLE_SPEED` (via tolerant parse, name "GOOGLE_TTS_SPEED"), `GOOGLE_LIMIT`
  (`usage.parse_limit(env GOOGLE_TTS_MONTHLY_CHAR_LIMIT)`), `GOOGLE_USAGE_FILE`
  (env `GOOGLE_TTS_USAGE_FILE`, default `~/.config/claude-code-tts/google-usage.json`).
- `_make_provider`: add `google` branch:
  `GoogleWithFallback(GoogleTTSProvider(GOOGLE_VOICE, GOOGLE_SPEED, keychain.get_google_key()),
  EdgeTTSProvider(voice=VOICE, rate=RATE, pitch=PITCH), UsageCounter(GOOGLE_USAGE_FILE, GOOGLE_LIMIT),
  notify_mod.send)` — access `notify_mod.send` as a module attribute at build time so tests can patch
  it before reload. Unknown TTS_PROVIDER still falls back to edge-tts with a log line.
- Update the header docstring (providers list, "on provider failure return 5xx" now notes that the
  google wrapper falls back to edge first, dependencies + httpx). Update providers/__init__.py docstring.
- `/health` and `/` need no change (`describe()` carries usage/last_fallback; keep `provider` and
  `voice` keys because `/` reads them).

### 8.7 bin/tts
- Header comment: three providers. Add `KEYCHAIN_ACCOUNT_GOOGLE="google"`.
- Shared helpers: `_key_shape_problem <account>` (empty output = OK), `_warn_key_unusable <openai|google>`
  (replace `_warn_openai_key_unusable`), `_store_key <account> <label> <cmd>` implementing decision 9,
  `_clear_key <account> <label>`. Remove `_validate_openai_key` and the stale argv comment block
  (check `doctor` does not still use it; if so fold into the shape helper).
- `cmd_openai_key`, `cmd_google_key`, `cmd_openai_key_clear`, `cmd_google_key_clear` = thin wrappers.
- `cmd_provider`: accept `edge-tts|openai|google`; on google call `_warn_key_unusable google || true`.
- `cmd_google_voice <name>`: validate `^[a-z]{2,3}-[A-Z]{2}-Chirp3-HD-[A-Za-z]+$`, `_update_env GOOGLE_TTS_VOICE`.
- `cmd_google_speed <val>`: same validation style as `cmd_speed` (numeric regex + awk bounds) with 0.25-2.0,
  `_update_env GOOGLE_TTS_SPEED`.
- `cmd_status`: after the server line, with jq and `// empty` guards, print
  `google:  <used>/<limit> chars (<percent>%) month=<YYYY-MM> (Pacific)`, an `exhausted -> edge-tts until next month`
  line when the flag is set, and `fallback: <reason> at <ts>` when `last_fallback` is non-null.
- `cmd_config`: add `GOOGLE_TTS_VOICE GOOGLE_TTS_SPEED GOOGLE_TTS_MONTHLY_CHAR_LIMIT GOOGLE_TTS_USAGE_FILE`
  to `keys`, defaults in `_default_for_key` (`ru-RU-Chirp3-HD-Aoede`, `1.0`, `990000`, the default path);
  widen the printf column (longest name is 29 chars); add a "Google key" line beside the OpenAI one.
- `cmd_doctor`: new Google section — httpx importable (fail if provider is google else warn), key present +
  shape OK (fail if provider is google and missing, else warn), voice regex, speed in range, usage file parses
  (`jq -e .chars`), zoneinfo loads via the venv python (warn otherwise).
- `COMMANDS`: add `google-key`, `google-key-clear`, `google-voice`, `google-speed`; update the `provider` help.
- Validate with `bash -n bin/tts` (and shellcheck if installed). Do NOT execute install/on.

### 8.8 Docs
- `docs/CONFIGURATION.md`: provider table row; "google knobs" section (commands, env vars, Keychain account,
  free tier + limit semantics, glossary table from section 3, warning tiers, sticky exhaustion, usage file
  format, Pacific month, how to reset: `rm ~/.config/claude-code-tts/google-usage.json`, note that
  failure-based fallbacks do not notify); replace the now-wrong line "There is no automatic fallback to
  edge-tts" (OpenAI has none; Google does); add env rows; fix the stale OpenAI default-model/voices text only
  where you touch it (code default is gpt-4o-mini-tts).
- `docs/ARCHITECTURE.md`: data-flow diagram with wrapper/counter/fallback; file-locations table gets the usage file.
- `README.md`: adjust "zero cloud keys, zero paid APIs" (already inaccurate), add the google-* commands and env rows.
- `CLAUDE.md` Layout: mention usage.py, notify.py, providers/google*.py. (Optionally run the `claude-md-sync` skill at the end.)

## 9. Edge cases (all must be handled and tested)

| Situation | Behavior |
|---|---|
| Mic lock present before synthesis | `_speak` returns 423 before the provider is called: nothing counted, no flags, no notification. |
| Lock appears during synthesis | Chars already counted; one-shot flags consumed; notification already shown; MP3 dropped by the server. Accepted. |
| Google fails after counting | Count kept (no refund); same text (phrase included) goes to edge; last_fallback set. |
| Missing key / >5000 bytes | Straight to edge, not counted, logged. |
| Exhausted flag set | Straight to edge for the rest of the month; no repeated notifications. |
| One request jumps past 80 and 90 | Both flags set; only the 90 phrase spoken. |
| Server started already past 90% | Flags already persisted (or set on first reserve); no duplicate spam. |
| Phrase pushes text over 5000 bytes | Phrase dropped, notification still sent, phrase chars not counted. |
| Corrupt usage file | Loud log, renamed `.corrupt`, restart from 0 (may under-count; accepted). |
| Unwritable usage dir | Logged, swallowed, speech continues. |
| Bad GOOGLE_TTS_MONTHLY_CHAR_LIMIT / SPEED env | Logged, default used, import never crashes. |
| Concurrent /speak | `reserve` serialized by the lock; 20 threads x reserve(1) with limit 5 => exactly 5 successes. |
| Month rollover (Pacific) | Detected on the next operation; chars, flags (incl. exhausted) reset. |
| Google outage | Each request waits up to ~10s before falling back; no circuit breaker (possible later). |
| Both Google and edge fail | edge ProviderError propagates -> HTTP 502 as today. |
| Non-macOS CI | notify swallows FileNotFoundError; tests inject a recorder anyway. |

## 10. Test plan (hand-written fakes; CI is Linux py3.10-3.13)

- `tests/test_usage.py`: fresh/missing file; persistence across instances; reserve over limit returns None
  and counts nothing; exactly-at-limit allowed, +1 not; Pacific boundary with injected clock
  (`2026-10-01T06:59Z` is still 2026-09, `07:00Z` is 2026-10) and year boundary; rollover resets chars+flags;
  corrupt/wrong-type file (log + `.corrupt` rename); thresholds set once and persist across instances; jump over
  both 80 and 90; `claim_once`/`flag_set` semantics incl. reset next month; `add` unconditional; atomic write leaves
  no tmp file; write failure (path is a dir) does not raise; 20-thread test; `parse_limit` cases
  (`"990000"`, `"990,000"`, `"990_000"`, `"1e6"`, `""`, `"abc"`, `"-5"`); ZoneInfo LA loads; tz fallback path
  (monkeypatch ZoneInfo to raise in the usage module).
- `tests/test_google_provider.py`: URL, `X-Goog-Api-Key` header, body shape, languageCode derivation, speakingRate;
  audio bytes written; errors (429, 500, 403 JSON body, `httpx.ConnectError`, non-JSON, missing audioContent, bad
  base64) all raise ProviderError; key never in error text or `describe()`; speed clamp logged; missing key raises
  without network; >5000 bytes (multi-byte Cyrillic) raises; use `httpx.MockTransport` by monkeypatching
  `google_mod.httpx.AsyncClient` with a factory that injects the transport.
- `tests/test_google_fallback.py` (FakeGoogle, FakeEdge, real UsageCounter on tmp_path + FakeClock, notify recorder):
  happy path counts len(text); pre-check failure -> edge + switch phrase + ONE notification; sticky: next requests go
  to edge without notifying even for short texts; Google error -> edge with same text, no refund, last_fallback set;
  no key / oversize -> edge, not counted; 80% once, 90% once, no repeats; jump over both; critical >= 98% notifies and
  speaks on EVERY message with correct rounded remaining; phrase chars counted; phrase dropped when over 5000 bytes
  (still notifies, not counted); raising notify does not break synthesis; month rollover re-arms warnings and clears
  exhausted; edge failure propagates; `describe()` shape and no secrets; `round_remaining` table.
- `tests/test_keychain.py`: monkeypatch `subprocess.run`; `get_key("google")` argv, None on non-zero exit, strips
  whitespace; wrappers delegate.
- `tests/test_server.py`: new `google_server` fixture (TTS_PROVIDER=google, GOOGLE_TTS_USAGE_FILE in tmp_path,
  patched `keychain.get_google_key`, `notify.send`, httpx client, edge, afplay, then reload): /health has
  `config.usage`; /speak -> 202 and usage file grows; Google 500 still -> 202 via edge and /health shows
  last_fallback; lock file -> 423 and count unchanged; bad limit/speed env does not crash reload; unknown provider
  still falls back; all existing OpenAI/edge tests stay green.

## 11. Build sequence

1. [ ] Enter the existing worktree (section 5). Work only inside it.
2. [ ] keychain.py generalization (+ delete dead setters after grep) + tests/test_keychain.py.
3. [ ] usage.py + tests/test_usage.py.
4. [ ] notify.py.
5. [ ] providers/google.py + tests; add httpx to requirements.txt, tzdata to requirements-dev.txt.
6. [ ] providers/google_fallback.py + tests.
7. [ ] server.py wiring + google_server tests + header docs. Run pytest.
8. [ ] bin/tts (helpers, key commands, provider, google-voice/speed, status, config, doctor, COMMANDS, headers);
       `bash -n bin/tts`.
9. [ ] install.sh comment only; verify uninstall.sh --purge covers the usage file.
10. [ ] Docs (CONFIGURATION, ARCHITECTURE, README, CLAUDE.md Layout).
11. [ ] `/verify` (ruff format, ruff check, pytest). Fix everything.
12. [ ] Manual smoke test WITHOUT touching launchd/settings (see below), with the user's OK for a live call.
13. [ ] `/code-review`; fix findings the user agrees with.
14. [ ] Ask the user whether to keep this plan file; commit (`feat(google): ...`), push branch,
       `gh pr create --base main`; watch CI (use Monitor rather than a blocking poll loop). Tell the user how
       to roll out after merge (below).

Manual smoke test (does not touch the installed launchd agent): from the worktree, using the main checkout's venv
(httpx is already installed via openai):
`TTS_PORT=7899 TTS_PROVIDER=google GOOGLE_TTS_USAGE_FILE=<scratch>/usage.json PYTHONPATH=<worktree>/src /Users/kos/dev/claude-code-tts/venv/bin/python -m claude_code_tts.server`
then `curl -X POST 127.0.0.1:7899/speak -d '{"text":"..."}'` (TTS_TLDR_ONLY=0 or include a TL;DR). Hand-edit the
scratch usage file to ~79.9%, 89.9%, 98%, and just under/over 990k to hear/see each tier, the sticky switch, and
`GET /health`. A live Google call spends a few free-tier characters: ask the user first.

Rollout after merge (user does this; do not do it for them): `git pull` in the main checkout,
`pip install -r requirements.txt` (or re-run install.sh), `tts provider google`, `tts restart`, check `tts status`
and `tts doctor`.

## 12. Unverified assumptions / risks (check, and tell the user what you find)

- `osascript display notification` from a launchd LaunchAgent: expected to work but never tested; may appear as
  "Script Editor" and need notification permission. Needs a manual check on the user's Mac after rollout.
- Google's free-tier month boundary: assumed Pacific Time calendar month; confirm in official docs.
- `security add-generic-password ... -w` (no value) prompts twice on the tty; a previous comment in bin/tts claims
  stdin is ignored and /dev/tty is read. Test the prompt flow with a THROWAWAY account, then delete it.
- Chirp 3 HD max input 5000 bytes and speakingRate 0.25-2.0 come from Google docs/search results; confirm quickly
  against https://docs.cloud.google.com/text-to-speech/docs/chirp3-hd and .../quotas if anything looks off.
- The local counter can drift from Google's real billing (other clients using the same key, deleted usage file,
  no refunds on failures). The 10,000-char margin absorbs small drift. One max-length overshoot would cost roughly
  $0.045 at ~$30 per 1M chars (price from third-party aggregators; official page not verified).
- Behavior change to call out in the PR: `tts openai-key` now uses the tty prompt + post-store shape check
  (previously validated before storing and passed the key via argv).

## 13. Definition of done

- All items in section 11 checked; `/verify` green locally and CI green on 3.10-3.13.
- No changes to ~/.claude/settings.json, plist template, or the user's real Keychain items.
- Every stale comment/doc line mentioning "two providers"/"no fallback" updated.
- PR opened against main from `feat/google-chirp-provider` with a summary that includes the behavior change above
  and the unverified-assumption list.
- Final report to the user in Russian following the user's format (done / needed from user / next steps /
  one improvement / one Claude Code tip / TL;DR last).
