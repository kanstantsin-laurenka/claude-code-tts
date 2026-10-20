"""
Unit tests for TTS providers. Both backends are stubbed out — no network,
no Keychain, no audio. Focuses on the provider contract (synthesize writes
to path / raises ProviderError) and the describe() shape used by /health.
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import aiohttp
import pytest
from edge_tts import exceptions as edge_exceptions

from claude_code_tts.providers import ProviderError
from claude_code_tts.providers.edge import EdgeTTSProvider, classify_edge_error
from claude_code_tts.providers.openai import SPEED_MAX, SPEED_MIN, OpenAIProvider


class _FakeEdgeCommunicate:
    def __init__(self, *a, **kw):
        self.text = a[0] if a else kw.get("text")

    async def save(self, path):
        Path(path).write_bytes(b"EDGE")


def test_edge_describe():
    p = EdgeTTSProvider(voice="ru-RU-SvetlanaNeural", rate="+0%", pitch="+0Hz")
    d = p.describe()
    assert d == {
        "provider": "edge-tts",
        "voice": "ru-RU-SvetlanaNeural",
        "rate": "+0%",
        "pitch": "+0Hz",
    }


def test_edge_synthesize_writes_file(tmp_path, monkeypatch):
    import claude_code_tts.providers.edge as edge_mod

    monkeypatch.setattr(edge_mod.edge_tts, "Communicate", _FakeEdgeCommunicate)

    p = EdgeTTSProvider(voice="ru-RU-SvetlanaNeural", rate="+0%", pitch="+0Hz")
    out = tmp_path / "out.mp3"
    asyncio.run(p.synthesize("Привет", str(out)))
    assert out.read_bytes() == b"EDGE"


def test_edge_wraps_errors_as_provider_error(tmp_path, monkeypatch):
    import claude_code_tts.providers.edge as edge_mod

    class _Boom:
        def __init__(self, *a, **kw):
            pass

        async def save(self, path):
            raise RuntimeError("network down")

    monkeypatch.setattr(edge_mod.edge_tts, "Communicate", _Boom)

    p = EdgeTTSProvider(voice="x", rate="+0%", pitch="+0Hz")
    with pytest.raises(ProviderError) as ex:
        asyncio.run(p.synthesize("hi", str(tmp_path / "x.mp3")))
    assert "network down" in str(ex.value)


def _handshake_403() -> aiohttp.WSServerHandshakeError:
    return aiohttp.WSServerHandshakeError(
        request_info=MagicMock(), history=(), status=403, message="Invalid response status"
    )


@pytest.mark.parametrize(
    ("exc", "kind", "detail"),
    [
        (_handshake_403(), "service", "HTTP 403"),
        (edge_exceptions.NoAudioReceived("no audio"), "service", "NoAudioReceived"),
        (aiohttp.ServerDisconnectedError(), "network", "ServerDisconnectedError"),
        (TimeoutError(), "network", "TimeoutError"),
        (ValueError("Invalid voice"), "config", "ValueError"),
        (RuntimeError("boom"), "unknown", "RuntimeError"),
    ],
)
def test_classify_edge_error(exc, kind, detail):
    assert classify_edge_error(exc) == (kind, detail)


class _FakeAlerter:
    def __init__(self) -> None:
        self.reports: list[tuple[str, str]] = []

    def report(self, installed: str, detail: str) -> None:
        self.reports.append((installed, detail))


def _failing_communicate(exc: Exception):
    class _Failing:
        def __init__(self, *a, **kw):
            pass

        async def save(self, path):
            raise exc

    return _Failing


def test_edge_service_failure_alerts_and_names_version(tmp_path, monkeypatch):
    import claude_code_tts.providers.edge as edge_mod

    monkeypatch.setattr(edge_mod.edge_tts, "Communicate", _failing_communicate(_handshake_403()))
    alerter = _FakeAlerter()
    p = EdgeTTSProvider(voice="x", rate="+0%", pitch="+0Hz", alerter=alerter)

    with pytest.raises(ProviderError) as ex:
        asyncio.run(p.synthesize("hi", str(tmp_path / "x.mp3")))

    version = edge_mod.installed_edge_version()
    assert alerter.reports == [(version, "HTTP 403")]
    assert f"edge-tts {version} error: Microsoft rejected the request (HTTP 403)" in str(ex.value)


@pytest.mark.parametrize("exc", [TimeoutError(), ValueError("Invalid voice")])
def test_edge_network_and_config_failures_do_not_alert(tmp_path, monkeypatch, exc):
    import claude_code_tts.providers.edge as edge_mod

    monkeypatch.setattr(edge_mod.edge_tts, "Communicate", _failing_communicate(exc))
    alerter = _FakeAlerter()
    p = EdgeTTSProvider(voice="x", rate="+0%", pitch="+0Hz", alerter=alerter)

    with pytest.raises(ProviderError):
        asyncio.run(p.synthesize("hi", str(tmp_path / "x.mp3")))

    assert alerter.reports == []


def test_openai_describe_includes_config_not_key():
    p = OpenAIProvider(model="tts-1", voice="nova", speed=1.25, api_key="sk-secret")
    d = p.describe()
    assert d["provider"] == "openai"
    assert d["model"] == "tts-1"
    assert d["voice"] == "nova"
    assert d["speed"] == 1.25
    assert d["key_loaded"] is True
    assert "api_key" not in d
    assert "sk-secret" not in repr(d)


def test_openai_speed_clamped(capsys):
    lo = OpenAIProvider(model="tts-1", voice="nova", speed=0.01, api_key="k")
    hi = OpenAIProvider(model="tts-1", voice="nova", speed=99.0, api_key="k")
    assert lo.speed == SPEED_MIN
    assert hi.speed == SPEED_MAX
    err = capsys.readouterr().err
    assert err.count("clamped to") == 2


def test_openai_speed_in_range_no_log(capsys):
    OpenAIProvider(model="tts-1", voice="nova", speed=1.25, api_key="k")
    assert "clamped" not in capsys.readouterr().err


def test_openai_missing_key_marks_not_loaded():
    p = OpenAIProvider(model="tts-1", voice="nova", speed=1.0, api_key=None)
    assert p.describe()["key_loaded"] is False


def test_openai_missing_key_raises_on_synthesize(tmp_path):
    p = OpenAIProvider(model="tts-1", voice="nova", speed=1.0, api_key=None)
    with pytest.raises(ProviderError) as ex:
        asyncio.run(p.synthesize("hi", str(tmp_path / "x.mp3")))
    assert "key" in str(ex.value).lower()


def test_openai_synthesize_writes_file(tmp_path, monkeypatch):
    import claude_code_tts.providers.openai as openai_mod

    class _Resp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def stream_to_file(self, path):
            Path(path).write_bytes(b"OPENAI")

    class _Streaming:
        def create(self, **kw):
            # Capture args to assert we're passing the right thing through.
            self.last = kw
            return _Resp()

    class _Speech:
        def __init__(self):
            self.with_streaming_response = _Streaming()

    class _Audio:
        def __init__(self):
            self.speech = _Speech()

    class _Client:
        def __init__(self, *a, **kw):
            self.audio = _Audio()

    monkeypatch.setattr(openai_mod, "AsyncOpenAI", _Client)

    p = OpenAIProvider(model="tts-1", voice="nova", speed=1.5, api_key="sk-x")
    out = tmp_path / "out.mp3"
    asyncio.run(p.synthesize("hello", str(out)))
    assert out.read_bytes() == b"OPENAI"


# ---------------------------------------------------------------------------
# instructions (gpt-4o-mini-tts only — silently dropped for tts-1 / tts-1-hd)
# ---------------------------------------------------------------------------


class _RecordingStreamClient:
    """AsyncOpenAI stand-in that captures create()'s kwargs on .last_kwargs."""

    class _Resp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def stream_to_file(self, path):
            Path(path).write_bytes(b"OK")

    class _Streaming:
        def __init__(self):
            self.last_kwargs: dict | None = None

        def create(self, **kw):
            self.last_kwargs = kw
            return _RecordingStreamClient._Resp()

    class _Speech:
        def __init__(self, streaming):
            self.with_streaming_response = streaming

    class _Audio:
        def __init__(self, streaming):
            self.speech = _RecordingStreamClient._Speech(streaming)

    def __init__(self, *_a, **_kw) -> None:
        self._streaming = _RecordingStreamClient._Streaming()
        self.audio = _RecordingStreamClient._Audio(self._streaming)

    @property
    def last_kwargs(self) -> dict | None:
        return self._streaming.last_kwargs


def _install_recording_client(monkeypatch) -> None:
    import claude_code_tts.providers.openai as openai_mod

    monkeypatch.setattr(openai_mod, "AsyncOpenAI", _RecordingStreamClient)


def test_instructions_forwarded_for_gpt_4o_mini(tmp_path, monkeypatch):
    _install_recording_client(monkeypatch)
    p = OpenAIProvider(
        model="gpt-4o-mini-tts",
        voice="ballad",
        speed=1.0,
        api_key="sk-x",
        instructions="Speak calmly with a warm tone",
    )
    asyncio.run(p.synthesize("hi", str(tmp_path / "o.mp3")))
    assert p._client.last_kwargs["instructions"] == "Speak calmly with a warm tone"
    assert p.describe()["instructions_active"] is True


def test_instructions_dropped_for_tts_1(tmp_path, monkeypatch, capsys):
    _install_recording_client(monkeypatch)
    p = OpenAIProvider(
        model="tts-1",
        voice="nova",
        speed=1.0,
        api_key="sk-x",
        instructions="Speak calmly",
    )
    # One-shot warn at construction so the user isn't surprised later.
    assert "instructions ignored" in capsys.readouterr().err.lower()

    asyncio.run(p.synthesize("hi", str(tmp_path / "o.mp3")))
    # API call must NOT carry `instructions` — that's the 400 we avoid.
    assert "instructions" not in p._client.last_kwargs
    assert p.describe()["instructions_active"] is False


def test_instructions_empty_not_forwarded(tmp_path, monkeypatch):
    _install_recording_client(monkeypatch)
    p = OpenAIProvider(
        model="gpt-4o-mini-tts",
        voice="nova",
        speed=1.0,
        api_key="sk-x",
        instructions="   ",
    )
    asyncio.run(p.synthesize("hi", str(tmp_path / "o.mp3")))
    assert "instructions" not in p._client.last_kwargs
    assert p.describe()["instructions_active"] is False


def test_instructions_omitted_by_default(tmp_path, monkeypatch):
    _install_recording_client(monkeypatch)
    p = OpenAIProvider(model="gpt-4o-mini-tts", voice="nova", speed=1.0, api_key="sk-x")
    asyncio.run(p.synthesize("hi", str(tmp_path / "o.mp3")))
    assert "instructions" not in p._client.last_kwargs
    assert p.describe()["instructions_active"] is False
