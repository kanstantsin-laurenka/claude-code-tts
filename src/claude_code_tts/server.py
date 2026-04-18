"""
TTS SERVER

HTTP bridge between Claude Code Stop-hook (hooks/tts-stop.sh) and Microsoft edge-tts.

Business rules:
  - Only ever one afplay process alive: a new /speak SIGTERMs the previous
    playback so rapid back-to-back responses don't overlap.
  - Text is sanitized via clean_text.clean_for_tts before synthesis so code
    blocks and markdown do not reach the voice model.
  - If TTS_LOCK_FILE exists (/tmp/nl-recording.lock by default) the server
    skips playback. Hammerspoon's whisper hotkey creates that lock while the
    microphone is recording — playing TTS during that window would leak the
    voice into the transcription.
  - On edge-tts/network failure, return 5xx and let the caller (tts-stop.sh)
    swallow it silently — Claude Code must never be blocked by TTS.

Dependencies: edge-tts (pip); afplay (macOS built-in).
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

import edge_tts

from .clean_text import clean_for_tts

HOST = "127.0.0.1"
PORT = int(os.environ.get("TTS_PORT", "7852"))

VOICE = os.environ.get("TTS_VOICE", "ru-RU-SvetlanaNeural")
RATE = os.environ.get("TTS_RATE", "+0%")
PITCH = os.environ.get("TTS_PITCH", "+0Hz")
MAX_CHARS = int(os.environ.get("TTS_MAX_CHARS", "1500"))

LOCK_FILE = os.environ.get("TTS_LOCK_FILE", "/tmp/nl-recording.lock")
RESPECT_LOCK = os.environ.get("TTS_RESPECT_LOCK", "1") == "1"

STATE_FILE = os.environ.get(
    "TTS_STATE_FILE",
    os.path.expanduser("~/.config/claude-code-tts/enabled"),
)


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


async def _synthesize(text: str, path: str) -> None:
    communicate = edge_tts.Communicate(text, VOICE, rate=RATE, pitch=PITCH)
    await communicate.save(path)


def _speak(text: str) -> tuple[int, str]:
    cleaned = clean_for_tts(text, max_chars=MAX_CHARS)
    if not cleaned:
        return 204, ""

    if _is_locked():
        log(f"skipping playback: lock file present at {LOCK_FILE}")
        return 423, "microphone recording in progress"

    fd, tmp_path = tempfile.mkstemp(prefix="tts_", suffix=".mp3")
    os.close(fd)

    try:
        asyncio.run(_synthesize(cleaned, tmp_path))
    except Exception as e:
        log(f"edge-tts failure: {e}")
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return 502, f"edge-tts error: {e}"

    # ⚠️ WARNING: re-check lock AFTER synthesis — a recording may have started
    # during the ~1s edge-tts call. Drop the MP3 instead of bleeding into the mic.
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

    log(f"speaking {len(cleaned)} chars with {VOICE}")
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
                    "voice": VOICE,
                    "rate": RATE,
                    "pitch": PITCH,
                    "lock_file": LOCK_FILE,
                    "respect_lock": RESPECT_LOCK,
                    "locked": _is_locked(),
                    "state_file": STATE_FILE,
                },
            )
            return
        if self.path == "/":
            msg = (
                f"Claude Code TTS server. Voice={VOICE} Rate={RATE} Pitch={PITCH}.\n"
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
    log(f"listening on http://{HOST}:{PORT} (voice={VOICE})")
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
