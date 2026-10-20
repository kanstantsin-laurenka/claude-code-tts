"""
EdgeOutageAlerter tests — fake clock, fake PyPI lookup, background work run inline.
No network, no osascript.
"""

import pytest

from claude_code_tts.providers.edge_alert import (
    EDGE_ALERT_INTERVAL_S,
    NOTIFICATION_TITLE,
    EdgeOutageAlerter,
    outage_message,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _alerter(latest="7.3.1", notify=None, clock=None):
    sent: list[tuple[str, str]] = []

    def record(title: str, message: str) -> None:
        sent.append((title, message))

    alerter = EdgeOutageAlerter(
        notify=notify or record,
        fetch_latest=latest if callable(latest) else (lambda: latest),
        clock=clock or _Clock(),
        run_in_background=lambda job: job(),
    )
    return alerter, sent


def test_newer_version_on_pypi_suggests_update():
    alerter, sent = _alerter(latest="7.3.1")

    alerter.report("7.2.8", "HTTP 403")

    assert sent == [
        (
            NOTIFICATION_TITLE,
            "edge-tts 7.2.8 не работает: сервер Microsoft отклоняет запросы (HTTP 403). "
            "Доступна версия 7.3.1 — выполните tts update",
        )
    ]


def test_same_version_blames_microsoft():
    alerter, sent = _alerter(latest="7.3.1")

    alerter.report("7.3.1", "NoAudioReceived")

    assert "Новой версии edge-tts пока нет" in sent[0][1]


def test_pypi_unreachable_suggests_checking():
    alerter, sent = _alerter(latest=None)

    alerter.report("7.3.1", "HTTP 403")

    assert sent[0][1].endswith("Проверьте обновления: tts update")


@pytest.mark.parametrize(
    ("installed", "latest", "newer"),
    [("7.2.8", "7.10.0", True), ("7.10.0", "7.9.9", False), ("7.3.1", "7.3.1", False)],
)
def test_versions_compare_numerically(installed, latest, newer):
    assert ("Доступна версия" in outage_message(installed, latest, "x")) is newer


def test_throttles_alerts_within_interval():
    clock = _Clock()
    alerter, sent = _alerter(clock=clock)

    alerter.report("7.3.1", "HTTP 403")
    clock.now += EDGE_ALERT_INTERVAL_S - 1
    alerter.report("7.3.1", "HTTP 403")
    assert len(sent) == 1

    clock.now += 1
    alerter.report("7.3.1", "HTTP 403")
    assert len(sent) == 2


def test_failing_lookup_and_notify_never_raise():
    def boom_lookup():
        raise RuntimeError("pypi down")

    def boom_notify(title, message):
        raise RuntimeError("osascript missing")

    alerter, _ = _alerter(latest=boom_lookup, notify=boom_notify)

    alerter.report("7.3.1", "HTTP 403")
