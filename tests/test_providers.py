"""
Unit tests for TTS providers. Both backends are stubbed out — no network,
no Keychain, no audio. Focuses on the provider contract (synthesize writes
to path / raises ProviderError) and the describe() shape used by /health.
"""

import asyncio
from pathlib import Path

import pytest

from claude_code_tts.providers import ProviderError
from claude_code_tts.providers.edge import EdgeTTSProvider
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


def test_openai_speed_clamped():
    lo = OpenAIProvider(model="tts-1", voice="nova", speed=0.01, api_key="k")
    hi = OpenAIProvider(model="tts-1", voice="nova", speed=99.0, api_key="k")
    assert lo.speed == SPEED_MIN
    assert hi.speed == SPEED_MAX


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
