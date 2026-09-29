"""
UsageCounter tests — injected clock, real files under tmp_path.
"""

import json
import os
import threading
from datetime import datetime, timezone

import pytest

import claude_code_tts.usage as usage_mod
from claude_code_tts.usage import UsageCounter, month_key, parse_limit


class FakeClock:
    def __init__(self, iso: str = "2026-09-15T12:00:00+00:00") -> None:
        self.now = datetime.fromisoformat(iso)

    def set(self, iso: str) -> None:
        self.now = datetime.fromisoformat(iso)

    def __call__(self) -> datetime:
        return self.now


def _counter(tmp_path, limit=1000, clock=None) -> UsageCounter:
    return UsageCounter(str(tmp_path / "usage.json"), limit, now=clock or FakeClock())


def test_missing_file_is_zero(tmp_path):
    snap = _counter(tmp_path).snapshot()
    assert snap["used"] == 0
    assert snap["month"] == "2026-09"
    assert snap["flags"] == []


def test_persists_across_instances(tmp_path):
    _counter(tmp_path).reserve(100)
    assert _counter(tmp_path).snapshot()["used"] == 100


def test_file_format(tmp_path):
    _counter(tmp_path).reserve(850)
    data = json.loads((tmp_path / "usage.json").read_text())
    assert data == {"version": 1, "month": "2026-09", "chars": 850, "flags": ["warn_80"]}


def test_reserve_over_limit_counts_nothing(tmp_path):
    c = _counter(tmp_path, limit=100)
    assert c.reserve(60) is not None
    assert c.reserve(41) is None
    assert c.snapshot()["used"] == 60


def test_exactly_at_limit_allowed_plus_one_not(tmp_path):
    c = _counter(tmp_path, limit=100)
    r = c.reserve(100)
    assert r is not None and r.remaining == 0
    assert c.reserve(1) is None


def test_pacific_month_boundary():
    assert month_key(datetime(2026, 10, 1, 6, 59, tzinfo=timezone.utc)) == "2026-09"
    assert month_key(datetime(2026, 10, 1, 7, 0, tzinfo=timezone.utc)) == "2026-10"


def test_pacific_year_boundary():
    # January is PST (UTC-8), so the year flips at 08:00Z.
    assert month_key(datetime(2027, 1, 1, 7, 59, tzinfo=timezone.utc)) == "2026-12"
    assert month_key(datetime(2027, 1, 1, 8, 0, tzinfo=timezone.utc)) == "2027-01"


def test_tz_fallback_when_zone_database_missing(monkeypatch):
    def boom(name):
        raise usage_mod.ZoneInfoNotFoundError(name)

    monkeypatch.setattr(usage_mod, "ZoneInfo", boom)
    monkeypatch.setattr(usage_mod, "_tz_fallback_logged", False)
    assert month_key(datetime(2026, 9, 15, tzinfo=timezone.utc)) == "2026-09"
    # Fixed UTC-8: 07:59Z on Oct 1 is still Sep 30 there.
    assert month_key(datetime(2026, 10, 1, 7, 59, tzinfo=timezone.utc)) == "2026-09"
    assert month_key(datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)) == "2026-10"


def test_pacific_zone_loads():
    # Guards CI: needs tzdata in requirements-dev.txt on Linux runners.
    usage_mod.ZoneInfo("America/Los_Angeles")


def test_rollover_resets_chars_and_flags(tmp_path):
    clock = FakeClock("2026-09-30T12:00:00+00:00")
    c = _counter(tmp_path, limit=100, clock=clock)
    c.reserve(95)
    c.claim_once("exhausted")
    assert c.flag_set("exhausted")
    clock.set("2026-10-01T12:00:00+00:00")
    assert c.snapshot()["used"] == 0
    assert not c.flag_set("exhausted")
    r = c.reserve(85)
    assert r is not None and r.crossed == (80,)


def test_corrupt_file_is_quarantined(tmp_path, capfd):
    path = tmp_path / "usage.json"
    path.write_text("{not json")
    assert _counter(tmp_path).snapshot()["used"] == 0
    assert (tmp_path / "usage.json.corrupt").exists()
    assert "corrupt" in capfd.readouterr().err


@pytest.mark.parametrize(
    "payload",
    [
        {"month": "2026-09", "chars": "12", "flags": []},
        {"month": "2026-09", "chars": True, "flags": []},
        {"month": "2026-09", "chars": -1, "flags": []},
        {"month": "2026-09", "chars": 1, "flags": "warn_80"},
        {"chars": 1, "flags": []},
        [1, 2],
    ],
)
def test_wrong_shape_is_quarantined(tmp_path, payload):
    (tmp_path / "usage.json").write_text(json.dumps(payload))
    assert _counter(tmp_path).snapshot()["used"] == 0
    assert (tmp_path / "usage.json.corrupt").exists()


def test_thresholds_fire_once_and_persist(tmp_path):
    c = _counter(tmp_path, limit=1000)
    assert c.reserve(700).crossed == ()
    assert c.reserve(100).crossed == (80,)
    assert c.reserve(1).crossed == ()
    assert _counter(tmp_path).reserve(1).crossed == ()  # new instance, same file
    assert c.reserve(100).crossed == (90,)
    assert c.reserve(1).crossed == ()


def test_jump_over_both_thresholds(tmp_path):
    r = _counter(tmp_path, limit=1000).reserve(950)
    assert r.crossed == (80, 90)
    assert _counter(tmp_path).snapshot()["flags"] == ["warn_80", "warn_90"]


def test_percent_at_least_is_integer_math(tmp_path):
    r = _counter(tmp_path, limit=1000).reserve(980)
    assert r.percent_at_least(98)
    r = _counter(tmp_path, limit=1000).reserve(1)  # 981
    assert r.percent_at_least(98)
    c = _counter(tmp_path / "other", limit=1000)
    assert not c.reserve(979).percent_at_least(98)


def test_claim_once_and_flag_set(tmp_path):
    c = _counter(tmp_path)
    assert not c.flag_set("exhausted")
    assert c.claim_once("exhausted") is True
    assert c.claim_once("exhausted") is False
    assert c.flag_set("exhausted")


def test_add_is_unconditional(tmp_path):
    c = _counter(tmp_path, limit=10)
    c.reserve(10)
    c.add(5)
    assert c.snapshot()["used"] == 15


def test_refund_lowers_count_but_keeps_flags(tmp_path):
    c = _counter(tmp_path, limit=1000)
    c.reserve(850)  # crosses 80%
    c.refund(100)
    snap = c.snapshot()
    assert snap["used"] == 750
    assert snap["flags"] == ["warn_80"]
    assert c.reserve(1).crossed == ()  # 80% must not re-fire after a refund


def test_refund_never_goes_negative(tmp_path):
    c = _counter(tmp_path)
    c.reserve(5)
    c.refund(50)
    assert c.snapshot()["used"] == 0


def test_atomic_write_leaves_no_tmp_files(tmp_path):
    c = _counter(tmp_path)
    for _ in range(5):
        c.reserve(1)
    assert os.listdir(tmp_path) == ["usage.json"]


def test_write_failure_does_not_raise(tmp_path, capfd):
    # A directory at the target path makes os.replace fail.
    target = tmp_path / "usage.json"
    target.mkdir()
    c = UsageCounter(str(target), 1000, now=FakeClock())
    assert c.reserve(10) is not None
    assert "cannot" in capfd.readouterr().err
    assert [p.name for p in tmp_path.iterdir()] == ["usage.json"]  # tmp cleaned up


def test_concurrent_reserve_is_exact(tmp_path):
    c = _counter(tmp_path, limit=5)
    results: list = []

    def worker():
        results.append(c.reserve(1))

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(r is not None for r in results) == 5
    assert c.snapshot()["used"] == 5


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("990000", 990_000),
        ("990,000", 990_000),
        ("990_000", 990_000),
        ("1e6", 1_000_000),
        (" 500 ", 500),
        ("", 990_000),
        ("abc", 990_000),
        ("-5", 990_000),
        ("0", 990_000),
        ("inf", 990_000),
        (None, 990_000),
    ],
)
def test_parse_limit(raw, expected):
    assert parse_limit(raw) == expected
