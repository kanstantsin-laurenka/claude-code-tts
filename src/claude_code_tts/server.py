"""
TTS SERVER

HTTP bridge between Claude Code Stop-hook (hooks/tts-stop.sh) and a pluggable
TTS provider (edge-tts by default; OpenAI or Google Chirp 3 HD optional).

Business rules:
  - Only ever one afplay process alive: a new /speak SIGTERMs the previous
    playback so rapid back-to-back responses don't overlap.
  - When TTS_TLDR_ONLY=1 (default), only the text after the last "TL;DR"
    marker is synthesized; if the message has no TL;DR the server stays
    silent (returns 204). This trains the assistant-side workflow to always
    emit a short summary block. TTS_TLDR_FALLBACK_TEXT (empty by default)
    overrides that silence with a fixed phrase — useful as a "task done,
    no summary" cue without losing the gate entirely for other messages.
  - Text is sanitized via clean_text.clean_for_tts before synthesis so code
    blocks and markdown do not reach the voice model.
  - If TTS_LOCK_FILE exists (/tmp/nl-recording.lock by default) the server
    skips playback. Hammerspoon's whisper hotkey creates that lock while the
    microphone is recording — playing TTS during that window would leak the
    voice into the transcription.
  - On provider failure (edge-tts/OpenAI/network), return 5xx and let the
    caller (tts-stop.sh) swallow it silently — Claude Code must never be
    blocked by TTS. Exception: the google provider is wrapped by
    GoogleWithFallback, which retries on edge-tts first, so a Google failure
    only surfaces as 5xx if edge-tts fails too.
  - Provider choice is frozen at module load: switching providers requires a
    launchd restart (`tts restart` or `tts provider ...`, which restarts).
  - Synthesis is not serialized (one handler thread per request), so anything
    shared between providers' requests (the Google usage counter) must be
    thread-safe.

Dependencies: edge-tts + openai + httpx (pip); afplay and osascript (macOS
  built-ins); `security` CLI for reading the OpenAI/Google keys from the Keychain.
Related: hooks/tts-stop.sh, ~/.claude/settings.json (Stop hook),
         /tmp/nl-recording.lock (written by Hammerspoon STT config in TTS2 project).
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import keychain
from . import notify as notify_mod
from . import usage as usage_mod
from .clean_text import clean_for_tts, extract_tldr
from .providers import ProviderError, TTSProvider
from .providers.edge import EdgeTTSProvider
from .providers.google import GoogleTTSProvider
from .providers.google_fallback import GoogleWithFallback
from .providers.openai import OpenAIProvider

HOST = "127.0.0.1"
PORT = int(os.environ.get("TTS_PORT", "7852"))

PROVIDER_NAME = os.environ.get("TTS_PROVIDER", "edge-tts")

VOICE = os.environ.get("TTS_VOICE", "ru-RU-SvetlanaNeural")
RATE = os.environ.get("TTS_RATE", "+0%")
PITCH = os.environ.get("TTS_PITCH", "+0Hz")

OPENAI_MODEL = os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_VOICE = os.environ.get("OPENAI_TTS_VOICE", "nova")
# Free-form tone/style hint, e.g. "Speak calmly in Russian with a warm tone".
# Only honored by gpt-4o-mini-tts — the provider drops it for tts-1(-hd) to
# avoid 400 errors, so the setting is safe to leave in the plist during
# temporary model switches.
OPENAI_INSTRUCTIONS = os.environ.get("OPENAI_TTS_INSTRUCTIONS", "")


def _parse_speed(raw: str, name: str = "OPENAI_TTS_SPEED") -> float:
    # A bad value here would otherwise crash module import → launchd would loop
    # restarts forever. Match the unknown-TTS_PROVIDER fallback below: log and
    # use the safe default.
    try:
        return float(raw)
    except ValueError:
        print(
            f"[tts] invalid {name}={raw!r}, using 1.0",
            file=sys.stderr,
            flush=True,
        )
        return 1.0


OPENAI_SPEED = _parse_speed(os.environ.get("OPENAI_TTS_SPEED", "1.0"))

GOOGLE_VOICE = os.environ.get("GOOGLE_TTS_VOICE", "ru-RU-Chirp3-HD-Aoede")
GOOGLE_SPEED = _parse_speed(os.environ.get("GOOGLE_TTS_SPEED", "1.0"), "GOOGLE_TTS_SPEED")
# Local guard under Google's 1M free tier; see usage.py for why it is 990k.
GOOGLE_LIMIT = usage_mod.parse_limit(os.environ.get("GOOGLE_TTS_MONTHLY_CHAR_LIMIT"))
GOOGLE_USAGE_FILE = os.environ.get(
    "GOOGLE_TTS_USAGE_FILE",
    os.path.expanduser("~/.config/claude-code-tts/google-usage.json"),
)

MAX_CHARS = int(os.environ.get("TTS_MAX_CHARS", "1500"))

TLDR_ONLY = os.environ.get("TTS_TLDR_ONLY", "1") == "1"
# Opt-in replacement for the 204-silence when TLDR_ONLY=1 and the message has
# no TL;DR block. Empty string (default) preserves the silent-skip behavior
# that trains the assistant to always emit a summary. A non-empty value is
# spoken as-is, bypassing the TL;DR gate for that one request — useful as a
# "task done" confirmation beep when the assistant forgot the TL;DR.
TLDR_FALLBACK_TEXT = os.environ.get("TTS_TLDR_FALLBACK_TEXT", "")

LOCK_FILE = os.environ.get("TTS_LOCK_FILE", "/tmp/nl-recording.lock")
RESPECT_LOCK = os.environ.get("TTS_RESPECT_LOCK", "1") == "1"


def _make_provider() -> TTSProvider:
    if PROVIDER_NAME == "openai":
        return OpenAIProvider(
            model=OPENAI_MODEL,
            voice=OPENAI_VOICE,
            speed=OPENAI_SPEED,
            api_key=keychain.get_openai_key(),
            instructions=OPENAI_INSTRUCTIONS,
        )
    if PROVIDER_NAME == "google":
        return GoogleWithFallback(
            GoogleTTSProvider(GOOGLE_VOICE, GOOGLE_SPEED, keychain.get_google_key()),
            EdgeTTSProvider(voice=VOICE, rate=RATE, pitch=PITCH),
            usage_mod.UsageCounter(GOOGLE_USAGE_FILE, GOOGLE_LIMIT),
            # Module attribute lookup at build time so tests can patch notify.send.
            notify_mod.send,
        )
    # Unknown values fall through to edge-tts so a typo in TTS_PROVIDER doesn't
    # brick the server — just log and use the free default.
    if PROVIDER_NAME != "edge-tts":
        print(
            f"[tts] unknown TTS_PROVIDER={PROVIDER_NAME!r}, falling back to edge-tts",
            file=sys.stderr,
            flush=True,
        )
    return EdgeTTSProvider(voice=VOICE, rate=RATE, pitch=PITCH)


PROVIDER: TTSProvider = _make_provider()


def _is_locked() -> bool:
    return RESPECT_LOCK and os.path.exists(LOCK_FILE)


_play_lock = threading.Lock()
_current_proc: subprocess.Popen | None = None
_current_tmp: str | None = None


def log(msg: str) -> None:
    print(f"[tts] {msg}", file=sys.stderr, flush=True)


def _stop_current() -> None:
    """Kill any active afplay and clean up its temp file. Caller holds _play_lock."""
    global _current_proc, _current_tmp
    if _current_proc and _current_proc.poll() is None:
        try:
            _current_proc.send_signal(signal.SIGTERM)
            _current_proc.wait(timeout=1.0)
        except Exception as e:
            log(f"failed to stop previous playback: {e}")
    _current_proc = None
    if _current_tmp and os.path.exists(_current_tmp):
        try:
            os.unlink(_current_tmp)
        except OSError:
            pass
    _current_tmp = None


def _speak(text: str) -> tuple[int, str]:
    if TLDR_ONLY:
        tldr = extract_tldr(text)
        if tldr is None:
            if TLDR_FALLBACK_TEXT:
                log(
                    f"no TL;DR block — speaking fallback "
                    f"({len(TLDR_FALLBACK_TEXT)} chars); preview={text[:80]!r}"
                )
                body_to_speak = TLDR_FALLBACK_TEXT
            else:
                log(
                    f"no TL;DR block found; skipping playback (TTS_TLDR_ONLY=1); "
                    f"preview={text[:80]!r}"
                )
                return 204, ""
        else:
            body_to_speak = tldr
    else:
        body_to_speak = text

    cleaned = clean_for_tts(body_to_speak, max_chars=MAX_CHARS)
    if not cleaned:
        return 204, ""

    if _is_locked():
        log(f"skipping playback: lock file present at {LOCK_FILE}")
        return 423, "microphone recording in progress"

    fd, tmp_path = tempfile.mkstemp(prefix="tts_", suffix=".mp3")
    os.close(fd)

    try:
        asyncio.run(PROVIDER.synthesize(cleaned, tmp_path))
    except ProviderError as e:
        log(str(e))
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return 502, str(e)
    except Exception as e:
        log(f"provider failure: {e}")
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return 502, f"provider error: {e}"

    # ⚠️ WARNING: re-check lock AFTER synthesis — a recording may have started
    # during the ~1s provider call. Drop the MP3 instead of bleeding into the mic.
    if _is_locked():
        log(f"dropping post-synthesis playback: lock appeared at {LOCK_FILE}")
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return 423, "microphone recording started during synthesis"

    global _current_proc, _current_tmp
    with _play_lock:
        _stop_current()
        try:
            _current_proc = subprocess.Popen(
                ["afplay", tmp_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _current_tmp = tmp_path
        except FileNotFoundError:
            log("afplay not found")
            os.unlink(tmp_path)
            return 500, "afplay missing"

    log(f"speaking {len(cleaned)} chars via {PROVIDER.name}")
    return 202, cleaned[:80]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # silence default access log
        return

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(
                200,
                {
                    "ok": True,
                    "config": PROVIDER.describe(),
                    "tldr_only": TLDR_ONLY,
                    "tldr_fallback": bool(TLDR_FALLBACK_TEXT),
                    "lock_file": LOCK_FILE,
                    "respect_lock": RESPECT_LOCK,
                    "locked": _is_locked(),
                },
            )
            return
        if self.path == "/":
            cfg = PROVIDER.describe()
            msg = (
                f"Claude Code TTS server. provider={cfg.get('provider')} "
                f"voice={cfg.get('voice')}.\n"
                'POST /speak {"text": "..."} | POST /stop | GET /health\n'
            )
            body = msg.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path == "/stop":
            with _play_lock:
                _stop_current()
            self._json(200, {"stopped": True})
            return

        if self.path != "/speak":
            self._json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._json(400, {"error": "empty body"})
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self._json(400, {"error": "invalid JSON"})
            return

        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str) or not text.strip():
            self._json(400, {"error": "missing 'text'"})
            return

        status, preview = _speak(text)
        if status == 204:
            self.send_response(204)
            self.end_headers()
            return
        if status >= 400:
            self._json(status, {"error": preview})
            return
        self._json(status, {"queued": True, "preview": preview})


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log(f"listening on http://{HOST}:{PORT} (provider={PROVIDER.name})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        with _play_lock:
            _stop_current()
        server.server_close()


if __name__ == "__main__":
    main()
