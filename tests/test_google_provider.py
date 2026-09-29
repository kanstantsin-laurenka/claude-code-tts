"""
GoogleTTSProvider tests — httpx.MockTransport injected by patching AsyncClient, no network.
"""

import asyncio
import base64
import json

import httpx
import pytest

import claude_code_tts.providers.google as google_mod
from claude_code_tts.providers import ProviderError

KEY = "AIzaSyFAKEKEYFAKEKEYFAKEKEYFAKEKEYFAKE0"
VOICE = "ru-RU-Chirp3-HD-Aoede"
AUDIO = b"ID3fake-mp3-bytes"


def _install(monkeypatch, handler):
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(wrapped)
        return real(*a, **kw)

    monkeypatch.setattr(google_mod.httpx, "AsyncClient", factory)
    return seen


def _ok(request):
    return httpx.Response(200, json={"audioContent": base64.b64encode(AUDIO).decode()})


def _run(provider, text, path):
    asyncio.run(provider.synthesize(text, str(path)))


def test_request_shape_and_audio_written(monkeypatch, tmp_path):
    seen = _install(monkeypatch, _ok)
    out = tmp_path / "o.mp3"
    _run(google_mod.GoogleTTSProvider(VOICE, 1.25, KEY), "Привет", out)

    req = seen[0]
    assert str(req.url) == google_mod.GOOGLE_URL
    assert req.headers["X-Goog-Api-Key"] == KEY
    assert KEY not in str(req.url)
    assert json.loads(req.content) == {
        "input": {"text": "Привет"},
        "voice": {"languageCode": "ru-RU", "name": VOICE},
        "audioConfig": {"audioEncoding": "MP3", "speakingRate": 1.25},
    }
    assert out.read_bytes() == AUDIO


def test_language_code_derivation():
    p = google_mod.GoogleTTSProvider("en-US-Chirp3-HD-Puck", 1.0, KEY)
    assert p.language_code == "en-US"


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: httpx.Response(429, text="quota"),
        lambda r: httpx.Response(500, text="boom"),
        lambda r: httpx.Response(403, json={"error": {"message": "API key not valid"}}),
        lambda r: httpx.Response(200, text="not json"),
        lambda r: httpx.Response(200, json={"nope": 1}),
        lambda r: httpx.Response(200, json={"audioContent": "@@@not-base64@@@"}),
        lambda r: httpx.Response(200, json={"audioContent": ""}),
        lambda r: httpx.Response(200, json=["list"]),
    ],
)
def test_errors_raise_provider_error_and_write_nothing(monkeypatch, tmp_path, handler):
    _install(monkeypatch, handler)
    out = tmp_path / "o.mp3"
    with pytest.raises(ProviderError) as exc:
        _run(google_mod.GoogleTTSProvider(VOICE, 1.0, KEY), "текст", out)
    assert KEY not in str(exc.value)
    assert not out.exists()


def test_connect_error_raises_provider_error(monkeypatch, tmp_path):
    def boom(request):
        raise httpx.ConnectError("no route", request=request)

    _install(monkeypatch, boom)
    with pytest.raises(ProviderError, match="network"):
        _run(google_mod.GoogleTTSProvider(VOICE, 1.0, KEY), "x", tmp_path / "o.mp3")


@pytest.mark.parametrize(
    ("handler", "billed"),
    [
        (lambda r: httpx.Response(400, text="bad"), False),
        (lambda r: httpx.Response(401, text="bad key"), False),
        (lambda r: httpx.Response(403, text="forbidden"), False),
        (lambda r: httpx.Response(429, text="quota"), False),
        (lambda r: httpx.Response(500, text="boom"), True),
        (lambda r: httpx.Response(503, text="down"), True),
        (lambda r: httpx.Response(200, text="not json"), True),
    ],
)
def test_provider_error_billed_flag_by_status(monkeypatch, tmp_path, handler, billed):
    _install(monkeypatch, handler)
    with pytest.raises(ProviderError) as exc:
        _run(google_mod.GoogleTTSProvider(VOICE, 1.0, KEY), "x", tmp_path / "o.mp3")
    assert exc.value.billed is billed


@pytest.mark.parametrize(
    ("error", "billed"),
    [
        (httpx.ConnectError("no route"), False),
        (httpx.ConnectTimeout("slow connect"), False),
        (httpx.ReadTimeout("slow read"), True),
    ],
)
def test_provider_error_billed_flag_by_transport_error(monkeypatch, tmp_path, error, billed):
    def boom(request):
        raise error

    _install(monkeypatch, boom)
    with pytest.raises(ProviderError) as exc:
        _run(google_mod.GoogleTTSProvider(VOICE, 1.0, KEY), "x", tmp_path / "o.mp3")
    assert exc.value.billed is billed


def test_http_error_message_is_truncated(monkeypatch, tmp_path):
    _install(monkeypatch, lambda r: httpx.Response(500, text="x" * 1000))
    with pytest.raises(ProviderError) as exc:
        _run(google_mod.GoogleTTSProvider(VOICE, 1.0, KEY), "x", tmp_path / "o.mp3")
    assert "HTTP 500" in str(exc.value)
    assert len(str(exc.value)) < 260


def test_missing_key_raises_without_network(monkeypatch, tmp_path):
    seen = _install(monkeypatch, _ok)
    with pytest.raises(ProviderError, match="key"):
        _run(google_mod.GoogleTTSProvider(VOICE, 1.0, None), "x", tmp_path / "o.mp3")
    assert seen == []


def test_oversize_multibyte_text_raises_without_network(monkeypatch, tmp_path):
    seen = _install(monkeypatch, _ok)
    text = "я" * 2501  # 5002 bytes, only 2501 chars
    p = google_mod.GoogleTTSProvider(VOICE, 1.0, KEY)
    assert p.unavailable_reason(text) == "text > 5000 bytes"
    with pytest.raises(ProviderError):
        _run(p, text, tmp_path / "o.mp3")
    assert seen == []


def test_exactly_5000_bytes_is_allowed():
    p = google_mod.GoogleTTSProvider(VOICE, 1.0, KEY)
    assert p.unavailable_reason("я" * 2500) is None
    assert p.unavailable_reason("x") is None


def test_unavailable_reason_no_key():
    assert google_mod.GoogleTTSProvider(VOICE, 1.0, None).unavailable_reason("x") == "no API key"


def test_speed_clamped_and_logged(capfd):
    assert google_mod.GoogleTTSProvider(VOICE, 5.0, KEY).speed == 2.0
    assert google_mod.GoogleTTSProvider(VOICE, 0.1, KEY).speed == 0.25
    err = capfd.readouterr().err
    assert "GOOGLE_TTS_SPEED" in err and "clamped" in err


def test_describe_never_contains_key():
    d = google_mod.GoogleTTSProvider(VOICE, 1.0, KEY).describe()
    assert d == {"provider": "google", "voice": VOICE, "speed": 1.0, "key_loaded": True}
    assert KEY not in json.dumps(d)
