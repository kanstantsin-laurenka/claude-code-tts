"""
EDGE-TTS OUTAGE ALERTS

Turns an edge-tts failure on Microsoft's side into a macOS notification that names the
cause and the fix, so a silent server does not leave the user guessing.

Business rules:
  - Only service failures alert (Microsoft rejected the request or changed the protocol —
    classified in providers/edge.py). Network and config errors are only logged: a
    dropped Wi-Fi would otherwise spam notifications, and `tts doctor` covers bad config.
  - At most one alert per EDGE_ALERT_INTERVAL_S. The timestamp lives in memory, so a
    server restart may alert once more — accepted, it is still far from spam.
  - The PyPI lookup and the notification run in the background: the failing request
    still answers its 502 immediately.
  - The text compares the installed edge-tts with the newest one on PyPI:
      newer exists  -> "run tts update" (the fix is already released);
      same version  -> outage on Microsoft's side, wait for an edge-tts fix;
      lookup failed -> suggest checking with `tts update`.
  - Never raises: alerting must not turn one failure into two.

Dependencies: httpx (PyPI lookup), a notify callable (notify.send in production).
Related: providers/edge.py (error classification), docs/CONFIGURATION.md#edge-tts-outages.
"""

import re
import sys
import threading
import time
from collections.abc import Callable

import httpx

NOTIFICATION_TITLE = "claude-code-tts"
# Long enough to cover a typical Microsoft-side outage (fixed within hours to a day or
# two) with a few reminders, short enough that a forgotten `tts update` gets nudged.
EDGE_ALERT_INTERVAL_S = 6 * 3600
PYPI_URL = "https://pypi.org/pypi/edge-tts/json"
PYPI_TIMEOUT_S = 3.0


def _log(msg: str) -> None:
    print(f"[tts] {msg}", file=sys.stderr, flush=True)


def fetch_latest_from_pypi() -> str | None:
    try:
        response = httpx.get(PYPI_URL, timeout=PYPI_TIMEOUT_S)
        response.raise_for_status()
        return response.json()["info"]["version"]
    except Exception as e:
        _log(f"edge-tts: PyPI version lookup failed: {e}")
        return None


def _version_key(version: str) -> tuple[int, ...]:
    # Numeric release parts only ("7.3.1" -> (7, 3, 1)); edge-tts publishes plain
    # X.Y.Z versions, so pre-release suffixes are not worth a `packaging` dependency.
    return tuple(int(part) for part in re.findall(r"\d+", version))


def outage_message(installed: str, latest: str | None, detail: str) -> str:
    head = f"edge-tts {installed} не работает: сервер Microsoft отклоняет запросы ({detail})."
    if latest is None:
        return f"{head} Проверьте обновления: tts update"
    if _version_key(latest) > _version_key(installed):
        return f"{head} Доступна версия {latest} — выполните tts update"
    return f"{head} Новой версии edge-tts пока нет — сбой на стороне Microsoft, ждём исправления"


def _start_daemon_thread(job: Callable[[], None]) -> None:
    threading.Thread(target=job, daemon=True).start()


class EdgeOutageAlerter:
    def __init__(
        self,
        notify: Callable[[str, str], None],
        fetch_latest: Callable[[], str | None] = fetch_latest_from_pypi,
        clock: Callable[[], float] = time.monotonic,
        run_in_background: Callable[[Callable[[], None]], None] = _start_daemon_thread,
    ) -> None:
        self._notify = notify
        self._fetch_latest = fetch_latest
        self._clock = clock
        self._run_in_background = run_in_background
        self._lock = threading.Lock()
        self._last_alert_at: float | None = None

    def report(self, installed: str, detail: str) -> None:
        now = self._clock()
        with self._lock:
            if (
                self._last_alert_at is not None
                and now - self._last_alert_at < EDGE_ALERT_INTERVAL_S
            ):
                return
            self._last_alert_at = now
        try:
            self._run_in_background(lambda: self._send(installed, detail))
        except Exception as e:
            _log(f"edge-tts: could not start outage alert: {e}")

    def _send(self, installed: str, detail: str) -> None:
        try:
            latest = self._fetch_latest()
        except Exception as e:
            _log(f"edge-tts: PyPI version lookup failed: {e}")
            latest = None
        message = outage_message(installed, latest, detail)
        _log(f"edge-tts outage alert: {message}")
        try:
            self._notify(NOTIFICATION_TITLE, message)
        except Exception as e:
            _log(f"notify failed: {e}")
