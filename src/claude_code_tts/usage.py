"""
USAGE COUNTER

Persistent per-month character counter that guards the Google Cloud TTS free tier
(1,000,000 chars/month). It only knows counts and one-shot flags; alert wording and
fallback policy live in providers/google_fallback.py.

Business rules:
  - The limit (default 990,000) sits 10,000 chars under Google's real free tier so drift
    between our local count and Google's accounting cannot cause a paid overage.
  - reserve() is atomic: a request that would cross the limit is refused and counts nothing.
  - Month = calendar month in America/Los_Angeles (believed to match Google's billing
    period). Chars and all flags (incl. "exhausted") reset when the month changes.
  - refund() returns chars for calls Google provably did not bill (see google_fallback.py).
  - Every operation re-reads the file, so deleting the file resets usage without a restart.
  - Persistence failures are logged and swallowed: speech must never break because the
    counter could not be saved (the counter may then under-count — accepted).

Dependencies: stdlib only (zoneinfo; `tzdata` is a dev-only dep for CI).
Related: providers/google_fallback.py, docs/CONFIGURATION.md (glossary of limits).
"""

import json
import os
import re
import sys
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_LIMIT = 990_000
WARN_THRESHOLDS = (80, 90)  # one-shot per month, percent of the limit
CRITICAL_PCT = 98  # not persisted: fires on every message
SCHEMA_VERSION = 1

_tz_fallback_logged = False


def _log(msg: str) -> None:
    print(f"[tts] {msg}", file=sys.stderr, flush=True)


def _pacific_tz():
    global _tz_fallback_logged
    try:
        return ZoneInfo("America/Los_Angeles")
    except ZoneInfoNotFoundError:
        if not _tz_fallback_logged:
            _tz_fallback_logged = True
            _log("tz database missing, using fixed UTC-8 for the Google usage month")
        # Off by one hour during DST; only shifts the month boundary by 1h.
        return timezone(timedelta(hours=-8))


def month_key(now: datetime) -> str:
    return now.astimezone(_pacific_tz()).strftime("%Y-%m")


def parse_limit(raw: str | None) -> int:
    """Tolerant parser: a bad env value must never crash server import."""
    if raw is None:
        return DEFAULT_LIMIT
    try:
        value = int(float(re.sub(r"[\s,_]", "", raw)))
    except (ValueError, OverflowError):
        value = 0
    if value <= 0:
        _log(f"invalid GOOGLE_TTS_MONTHLY_CHAR_LIMIT={raw!r}, using {DEFAULT_LIMIT}")
        return DEFAULT_LIMIT
    return value


@dataclass(frozen=True)
class Reservation:
    used: int  # after this reservation
    limit: int
    crossed: tuple[int, ...]  # one-shot thresholds newly crossed (flags already persisted)

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def percent_at_least(self, pct: int) -> bool:
        return self.used * 100 >= self.limit * pct


class UsageCounter:
    def __init__(
        self,
        path: str,
        limit: int,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._path = path
        self._limit = limit
        self._now = now or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()

    def reserve(self, n: int) -> Reservation | None:
        """Count n chars, or return None (counting nothing) if that would exceed the limit."""
        with self._lock:
            month, chars, flags = self._read()
            if chars + n > self._limit:
                return None
            chars += n
            crossed: list[int] = []
            for pct in WARN_THRESHOLDS:
                flag = f"warn_{pct}"
                # Set every threshold passed, so a single big jump doesn't re-fire the
                # lower one later.
                if chars * 100 >= self._limit * pct and flag not in flags:
                    flags.add(flag)
                    crossed.append(pct)
            self._write(month, chars, flags)
            return Reservation(used=chars, limit=self._limit, crossed=tuple(crossed))

    def add(self, n: int) -> None:
        """Count n chars unconditionally (an appended warning phrase already being sent)."""
        with self._lock:
            month, chars, flags = self._read()
            self._write(month, chars + n, flags)

    def refund(self, n: int) -> None:
        """Give back chars for a request Google provably did not bill.

        One-shot flags are deliberately NOT rolled back: a warning already spoken
        must not repeat. Clamped at 0 so a refund landing right after a month
        rollover cannot go negative.
        """
        with self._lock:
            month, chars, flags = self._read()
            self._write(month, max(0, chars - n), flags)

    def claim_once(self, flag: str) -> bool:
        """True only for the first caller each month; the claim is persisted."""
        with self._lock:
            month, chars, flags = self._read()
            if flag in flags:
                return False
            flags.add(flag)
            self._write(month, chars, flags)
            return True

    def flag_set(self, flag: str) -> bool:
        with self._lock:
            _, _, flags = self._read()
            return flag in flags

    def snapshot(self) -> dict:
        with self._lock:
            month, chars, flags = self._read()
        return {
            "month": month,
            "used": chars,
            "limit": self._limit,
            "percent": chars * 100 // self._limit,
            "remaining": max(0, self._limit - chars),
            "flags": sorted(flags),
        }

    # -- persistence (callers hold self._lock) --------------------------------------

    def _read(self) -> tuple[str, int, set[str]]:
        month = month_key(self._now())
        state = self._load_file()
        if state is None or state[0] != month:
            return month, 0, set()
        return month, state[1], state[2]

    def _load_file(self) -> tuple[str, int, set[str]] | None:
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = f.read()
        except FileNotFoundError:
            return None
        except OSError as e:
            _log(f"cannot read Google usage file {self._path}: {e}")
            return None
        try:
            data = json.loads(raw)
            month, chars, flags = data["month"], data["chars"], data["flags"]
            valid = (
                isinstance(month, str)
                and isinstance(chars, int)
                and not isinstance(chars, bool)
                and chars >= 0
                and isinstance(flags, list)
                and all(isinstance(x, str) for x in flags)
            )
            if not valid:
                raise ValueError("wrong field types")
        except (ValueError, KeyError, TypeError) as e:
            self._quarantine(e)
            return None
        return month, chars, set(flags)

    def _quarantine(self, err: Exception) -> None:
        # Loud on purpose: restarting from zero may under-count and the user should know.
        _log(f"corrupt Google usage file {self._path} ({err}); restarting count from 0")
        try:
            os.replace(self._path, self._path + ".corrupt")
        except OSError:
            pass

    def _write(self, month: str, chars: int, flags: set[str]) -> None:
        payload = {
            "version": SCHEMA_VERSION,
            "month": month,
            "chars": chars,
            "flags": sorted(flags),
        }
        tmp_path = None
        try:
            parent = os.path.dirname(self._path) or "."
            os.makedirs(parent, exist_ok=True)
            # Temp file + os.replace so a crash mid-write can't leave a truncated JSON.
            fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".google-usage-")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp_path, self._path)
            tmp_path = None
        except OSError as e:
            _log(f"cannot save Google usage file {self._path}: {e}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
