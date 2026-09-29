"""
GOOGLE WITH FALLBACK

Wraps GoogleTTSProvider with a monthly character quota, spoken/visual usage alerts and an
automatic fall back to edge-tts. ALL alert and fallback policy lives here; usage.py only
counts and notify.py only shows notifications.

Business rules (numbers are % of the LIMIT, default 990,000 — not of Google's 1,000,000):
  - Pre-check per request: if used + len(text) > limit the request goes to edge-tts and
    nothing is counted. The first such request sets a persisted "exhausted" flag; from then
    until the Pacific month rolls over EVERY request goes to edge-tts (no voice flip-flop).
  - Alerts: 80% and 90% once per month; >= 98% on every message; one notice at the switch.
    Each alert = a notification plus a spoken phrase APPENDED to the text (one MP3, one
    afplay — keeps the server's "only one afplay alive" invariant).
  - Appended phrase chars are counted (usage.add) only if the phrase was really sent. If
    text + phrase would exceed Google's 5000-byte cap the phrase is dropped, the
    notification still fires. The switch phrase is spoken by edge-tts and never counted.
  - Any Google failure (network, 4xx/5xx, timeout, bad body) retries the SAME request on
    edge-tts. Chars are counted BEFORE the call. They are refunded only when the error says
    Google provably did not bill (ProviderError.billed is False: connect errors, 4xx);
    5xx, read timeouts and bad responses keep the count because they may have been billed.
  - One-shot alerts (warn_80/warn_90/exhausted flags, notification, spoken phrase) are
    consumed when the request is reserved, before any audio exists. If the MP3 is later
    dropped (microphone lock, or a newer /speak interrupts playback) the spoken phrase is
    lost for the month; the notification still fired. Accepted: low severity, and re-arming
    would repeat alerts. A refund never un-sets a flag either.
  - A missing key or a text over 5000 bytes goes straight to edge-tts, uncounted.
  - Failure-based fallbacks do not notify: the user hears the voice change; /health shows
    fallback_count and last_fallback.

Dependencies: providers/google.py, usage.py, an edge-tts provider instance.
Related: docs/CONFIGURATION.md (glossary and alert table).
"""

import sys
import threading
from collections.abc import Callable
from datetime import datetime, timezone

from ..usage import CRITICAL_PCT, Reservation, UsageCounter
from . import ProviderError, TTSProvider
from .google import MAX_INPUT_BYTES, GoogleTTSProvider

NOTIFICATION_TITLE = "claude-code-tts"
EXHAUSTED_FLAG = "exhausted"

SWITCH_PHRASE = "Лимит Google исчерпан, перехожу на запасной голос."
SWITCH_NOTIFICATION = "Лимит Google TTS исчерпан, перехожу на edge-tts до конца месяца"


def warn_phrase(pct: int) -> str:
    return f"Внимание: использовано {pct} процентов лимита Google."


def warn_notification(pct: int) -> str:
    return f"Использовано {pct}% лимита Google TTS"


def critical_phrase(remaining: int) -> str:
    return (
        "Лимит Google почти исчерпан, до переключения на запасной голос "
        f"осталось около {remaining} символов."
    )


def critical_notification(remaining: int) -> str:
    return (
        f"Лимит Google TTS почти исчерпан: осталось около {remaining} символов "
        "до переключения на edge-tts"
    )


def round_remaining(n: int) -> int:
    """Round for speech: an exact figure like 19,803 sounds silly and is falsely precise."""
    if n < 1000:
        step = 10
    elif n < 10_000:
        step = 100
    else:
        step = 1000
    return (n + step // 2) // step * step


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _log(msg: str) -> None:
    print(f"[tts] {msg}", file=sys.stderr, flush=True)


class GoogleWithFallback(TTSProvider):
    name = "google"

    def __init__(
        self,
        google: GoogleTTSProvider,
        fallback: TTSProvider,
        usage: UsageCounter,
        notify: Callable[[str, str], None],
    ) -> None:
        self._google = google
        self._fallback = fallback
        self._usage = usage
        self._notify = notify
        self._stats_lock = threading.Lock()
        self._fallback_count = 0
        self._last_fallback: dict | None = None

    async def synthesize(self, text: str, out_path: str) -> None:
        # Checked BEFORE counting: these requests never reach Google, so they cost no quota.
        why = self._google.unavailable_reason(text)
        if why:
            return await self._edge(text, out_path, why)

        if self._usage.flag_set(EXHAUSTED_FLAG):
            return await self._edge(text, out_path, "monthly limit (exhausted)")

        reservation = self._usage.reserve(len(text))
        if reservation is None:
            if self._usage.claim_once(EXHAUSTED_FLAG):
                self._safe_notify(SWITCH_NOTIFICATION)
                text = f"{text} {SWITCH_PHRASE}"
            return await self._edge(text, out_path, "monthly limit")

        phrase, note = self._alert_for(reservation)
        if note:
            self._safe_notify(note)
        spoken = text
        counted = len(text)
        if phrase and _utf8_len(f"{text} {phrase}") <= MAX_INPUT_BYTES:
            spoken = f"{text} {phrase}"
            self._usage.add(len(phrase))
            counted += len(phrase)

        try:
            await self._google.synthesize(spoken, out_path)
        except Exception as e:
            # ⚠️ WARNING: catch everything, not just ProviderError — any Google-side surprise
            # must still produce speech.
            if isinstance(e, ProviderError) and not e.billed:
                # Without this a revoked key would burn the local quota on every request
                # and trigger a bogus "limit exhausted" switch for the rest of the month.
                self._usage.refund(counted)
            return await self._edge(spoken, out_path, str(e))

    def describe(self) -> dict:
        with self._stats_lock:
            count, last = self._fallback_count, self._last_fallback
        return {
            **self._google.describe(),
            "fallback": self._fallback.name,
            "usage": self._usage.snapshot(),
            "fallback_count": count,
            "last_fallback": last,
        }

    @staticmethod
    def _alert_for(r: Reservation) -> tuple[str, str]:
        if r.percent_at_least(CRITICAL_PCT):
            remaining = round_remaining(r.remaining)
            return critical_phrase(remaining), critical_notification(remaining)
        if r.crossed:
            # A jump over several thresholds speaks only the highest.
            pct = max(r.crossed)
            return warn_phrase(pct), warn_notification(pct)
        return "", ""

    def _safe_notify(self, message: str) -> None:
        try:
            self._notify(NOTIFICATION_TITLE, message)
        except Exception as e:
            _log(f"notify failed: {e}")

    async def _edge(self, text: str, out_path: str, reason: str) -> None:
        _log(f"google -> edge-tts fallback: {reason}")
        with self._stats_lock:
            self._fallback_count += 1
            self._last_fallback = {
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "reason": reason,
            }
        # An edge ProviderError propagates so the server answers 502 as it does today.
        await self._fallback.synthesize(text, out_path)
