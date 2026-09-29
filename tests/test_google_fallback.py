"""
GoogleWithFallback tests — hand-written fakes, a real UsageCounter on tmp_path and an
injected clock. No network, no osascript.
"""

import asyncio
import json
from datetime import datetime

import pytest

import claude_code_tts.providers.google_fallback as fb
from claude_code_tts.providers import ProviderError
from claude_code_tts.providers.google import MAX_INPUT_BYTES
from claude_code_tts.usage import UsageCounter

SECRET = "AIzaSECRETSECRETSECRETSECRETSECRET00"


class FakeClock:
    def __init__(self, iso="2026-09-15T12:00:00+00:00"):
        self.now = datetime.fromisoformat(iso)

    def __call__(self):
        return self.now


class FakeGoogle:
    def __init__(self, has_key=True, error: Exception | None = None):
        self.has_key = has_key
        self.error = error
        self.calls: list[str] = []

    def unavailable_reason(self, text):
        if not self.has_key:
            return "no API key"
        if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
            return "text > 5000 bytes"
        return None

    async def synthesize(self, text, out_path):
        self.calls.append(text)
        if self.error:
            raise self.error

    def describe(self):
        return {"provider": "google", "voice": "v", "speed": 1.0, "key_loaded": self.has_key}


class FakeEdge:
    name = "edge-tts"

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls: list[str] = []

    async def synthesize(self, text, out_path):
        self.calls.append(text)
        if self.error:
            raise self.error

    def describe(self):
        return {"provider": "edge-tts"}


class Env:
    def __init__(self, tmp_path, limit=100_000, google=None, edge=None, notify=None):
        self.clock = FakeClock()
        self.usage = UsageCounter(str(tmp_path / "u.json"), limit, now=self.clock)
        self.google = google or FakeGoogle()
        self.edge = edge or FakeEdge()
        self.notes: list[tuple[str, str]] = []
        self.provider = fb.GoogleWithFallback(
            self.google,
            self.edge,
            self.usage,
            notify or (lambda title, msg: self.notes.append((title, msg))),
        )

    def speak(self, text):
        asyncio.run(self.provider.synthesize(text, "/unused.mp3"))

    @property
    def used(self):
        return self.usage.snapshot()["used"]


def test_happy_path_counts_text_length(tmp_path):
    e = Env(tmp_path)
    e.speak("a" * 100)
    assert e.google.calls == ["a" * 100]
    assert e.edge.calls == []
    assert e.used == 100
    assert e.provider.describe()["fallback_count"] == 0


def test_precheck_failure_switches_with_one_notification(tmp_path):
    e = Env(tmp_path, limit=100)
    e.usage.add(95)
    e.speak("a" * 10)
    assert e.google.calls == []
    assert e.edge.calls == ["a" * 10 + " " + fb.SWITCH_PHRASE]
    assert e.used == 95  # nothing counted
    assert e.notes == [(fb.NOTIFICATION_TITLE, fb.SWITCH_NOTIFICATION)]


def test_exhaustion_is_sticky_even_for_short_texts(tmp_path):
    e = Env(tmp_path, limit=100)
    e.usage.add(95)
    e.speak("a" * 10)
    e.speak("b")  # would fit under the limit, but the month is exhausted
    e.speak("c")
    assert e.google.calls == []
    assert e.edge.calls[1:] == ["b", "c"]
    assert len(e.notes) == 1
    assert e.provider.describe()["last_fallback"]["reason"] == "monthly limit (exhausted)"


def test_google_error_falls_back_same_text_without_refund(tmp_path):
    e = Env(tmp_path, google=FakeGoogle(error=ProviderError("HTTP 500")))
    e.speak("привет")
    assert e.edge.calls == ["привет"]
    assert e.used == len("привет")
    d = e.provider.describe()
    assert d["fallback_count"] == 1
    assert d["last_fallback"]["reason"] == "HTTP 500"
    assert e.notes == []


def test_unbilled_error_refunds_counted_chars(tmp_path):
    e = Env(tmp_path, google=FakeGoogle(error=ProviderError("HTTP 401", billed=False)))
    e.speak("привет")
    assert e.edge.calls == ["привет"]
    assert e.used == 0


def test_unbilled_error_refunds_appended_phrase_too(tmp_path):
    e = Env(tmp_path, google=FakeGoogle(error=ProviderError("HTTP 401", billed=False)))
    e.usage.add(79_990)
    e.speak("a" * 20)  # crosses 80%, phrase appended, then Google rejects
    assert e.edge.calls[0].endswith(fb.warn_phrase(80))
    assert e.used == 79_990
    assert len(e.notes) == 1  # the alert already fired and is not re-armed


def test_revoked_key_does_not_burn_quota_or_trigger_switch(tmp_path):
    e = Env(tmp_path, limit=100, google=FakeGoogle(error=ProviderError("401", billed=False)))
    for _ in range(50):
        e.speak("a" * 10)
    assert e.used == 0
    assert not e.usage.flag_set(fb.EXHAUSTED_FLAG)
    assert e.notes == []


def test_billed_error_keeps_count(tmp_path):
    e = Env(tmp_path, google=FakeGoogle(error=ProviderError("HTTP 503", billed=True)))
    e.speak("привет")
    assert e.used == len("привет")


def test_unexpected_exception_also_falls_back(tmp_path):
    e = Env(tmp_path, google=FakeGoogle(error=ValueError("weird")))
    e.speak("x")
    assert e.edge.calls == ["x"]


def test_no_key_goes_to_edge_uncounted(tmp_path):
    e = Env(tmp_path, google=FakeGoogle(has_key=False))
    e.speak("hello")
    assert e.edge.calls == ["hello"]
    assert e.used == 0
    assert e.provider.describe()["last_fallback"]["reason"] == "no API key"


def test_oversize_goes_to_edge_uncounted(tmp_path):
    e = Env(tmp_path)
    e.speak("я" * 2501)
    assert e.google.calls == []
    assert len(e.edge.calls) == 1
    assert e.used == 0


def test_warning_80_once(tmp_path):
    e = Env(tmp_path)
    e.usage.add(79_990)
    e.speak("a" * 20)
    assert e.google.calls[0] == "a" * 20 + " " + fb.warn_phrase(80)
    assert e.notes == [(fb.NOTIFICATION_TITLE, fb.warn_notification(80))]
    e.speak("a" * 20)
    assert e.google.calls[1] == "a" * 20
    assert len(e.notes) == 1


def test_warning_90_once_after_80(tmp_path):
    e = Env(tmp_path)
    e.usage.add(79_990)
    e.speak("a" * 20)
    e.usage.add(10_000)
    e.speak("a" * 20)
    assert e.google.calls[1].endswith(fb.warn_phrase(90))
    e.speak("a" * 20)
    assert e.google.calls[2] == "a" * 20
    assert [n[1] for n in e.notes] == [fb.warn_notification(80), fb.warn_notification(90)]


def test_jump_over_80_and_90_speaks_only_90(tmp_path):
    e = Env(tmp_path)
    e.usage.add(89_990)
    e.speak("a" * 20)
    assert e.google.calls[0].endswith(fb.warn_phrase(90))
    assert [n[1] for n in e.notes] == [fb.warn_notification(90)]
    e.speak("a" * 20)  # 80 must not fire late
    assert len(e.notes) == 1


def test_phrase_chars_are_counted(tmp_path):
    e = Env(tmp_path)
    e.usage.add(79_990)
    e.speak("a" * 20)
    assert e.used == 79_990 + 20 + len(fb.warn_phrase(80))


def test_critical_alerts_on_every_message(tmp_path):
    e = Env(tmp_path)
    e.usage.add(97_990)
    e.speak("a" * 20)  # 98,010 used -> 1,990 remaining -> "about 2000"
    assert "около 2000 символов" in e.google.calls[0]
    e.speak("a" * 20)
    assert "почти исчерпан" in e.google.calls[1]
    assert len(e.notes) == 2
    assert all("до переключения на edge-tts" in n[1] for n in e.notes)


def test_phrase_dropped_when_over_byte_limit_but_still_notifies(tmp_path):
    e = Env(tmp_path, limit=1_000_000)
    e.usage.add(979_000)
    text = "a" * 4990  # room for the text but not for text + phrase
    e.speak(text)
    assert e.google.calls == [text]
    assert e.used == 979_000 + 4990  # phrase not counted
    assert len(e.notes) == 1


def test_raising_notify_does_not_break_synthesis(tmp_path):
    def boom(title, msg):
        raise RuntimeError("no notification center")

    e = Env(tmp_path, notify=boom)
    e.usage.add(79_990)
    e.speak("a" * 20)
    assert len(e.google.calls) == 1


def test_month_rollover_rearms_warnings_and_clears_exhausted(tmp_path):
    e = Env(tmp_path, limit=100_000)
    e.usage.add(79_990)
    e.speak("a" * 20)
    e.usage.claim_once(fb.EXHAUSTED_FLAG)
    e.speak("x")
    assert e.google.calls[1:] == []  # exhausted this month

    e.clock.now = datetime.fromisoformat("2026-10-02T12:00:00+00:00")
    e.speak("y")
    assert e.google.calls[-1] == "y"
    e.usage.add(79_990)
    e.speak("a" * 20)
    assert e.google.calls[-1].endswith(fb.warn_phrase(80))


def test_edge_failure_propagates(tmp_path):
    e = Env(tmp_path, google=FakeGoogle(has_key=False), edge=FakeEdge(ProviderError("edge down")))
    with pytest.raises(ProviderError, match="edge down"):
        e.speak("x")


def test_describe_shape_and_no_secrets(tmp_path):
    e = Env(tmp_path)
    e.speak("x")
    d = e.provider.describe()
    assert d["provider"] == "google"
    assert d["fallback"] == "edge-tts"
    assert d["last_fallback"] is None
    assert {"month", "used", "limit", "percent", "remaining", "flags"} <= set(d["usage"])
    assert SECRET not in json.dumps(d)


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (0, 0),
        (4, 0),
        (15, 20),
        (994, 990),
        (999, 1000),
        (1049, 1000),
        (1050, 1100),
        (9949, 9900),
        (9950, 10_000),
        (19_499, 19_000),
        (19_803, 20_000),
    ],
)
def test_round_remaining(n, expected):
    assert fb.round_remaining(n) == expected
