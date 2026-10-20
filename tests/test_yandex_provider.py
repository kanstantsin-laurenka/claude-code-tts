"""
YandexTTSProvider tests — httpx.MockTransport injected by patching AsyncClient, no network.
"""

import asyncio
import base64
import json

import httpx
import pytest

import claude_code_tts.providers.yandex as yandex_mod
from claude_code_tts.providers import ProviderError

KEY = "AQVNfakeFAKEfakeFAKEfakeFAKEfakeFAKEfake"
VOICE = "masha"
PART_1 = b"ID3fake-mp3-"
PART_2 = b"second-chunk"


def _install(monkeypatch, handler):
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(wrapped)
        return real(*a, **kw)

    monkeypatch.setattr(yandex_mod.httpx, "AsyncClient", factory)
    return seen


def _chunk(data: bytes) -> dict:
    return {"result": {"audioChunk": {"data": base64.b64encode(data).decode()}}}


def _stream(*messages: dict, sep: str = "\n") -> str:
    return sep.join(json.dumps(m) for m in messages)


def _ok(request):
    return httpx.Response(200, text=_stream(_chunk(PART_1), _chunk(PART_2)))


def _run(provider, text, path):
    asyncio.run(provider.synthesize(text, str(path)))


def test_request_shape_and_chunks_concatenated(monkeypatch, tmp_path):
    seen = _install(monkeypatch, _ok)
    out = tmp_path / "o.mp3"
    _run(yandex_mod.YandexTTSProvider(VOICE, "friendly", 1.25, KEY), "Привет", out)

    req = seen[0]
    assert str(req.url) == yandex_mod.REGION_URLS["ru"]
    assert req.headers["Authorization"] == f"Api-Key {KEY}"
    assert "x-folder-id" not in req.headers
    assert KEY not in str(req.url)
    assert json.loads(req.content) == {
        "text": "Привет",
        "hints": [{"voice": "masha"}, {"role": "friendly"}, {"speed": 1.25}],
        "outputAudioSpec": {"containerAudio": {"containerAudioType": "MP3"}},
        "unsafeMode": True,
    }
    assert out.read_bytes() == PART_1 + PART_2


@pytest.mark.parametrize(
    ("region", "url"),
    [
        ("ru", "https://tts.api.cloud.yandex.net/tts/v3/utteranceSynthesis"),
        ("kz", "https://tts.api.yandexcloud.kz/tts/v3/utteranceSynthesis"),
    ],
)
def test_region_selects_installation_endpoint(monkeypatch, tmp_path, region, url):
    seen = _install(monkeypatch, _ok)
    _run(yandex_mod.YandexTTSProvider(VOICE, "", 1.0, KEY, region), "x", tmp_path / "o.mp3")
    assert str(seen[0].url) == url


def test_unknown_region_falls_back_to_ru_and_logs(monkeypatch, tmp_path, capfd):
    seen = _install(monkeypatch, _ok)
    p = yandex_mod.YandexTTSProvider(VOICE, "", 1.0, KEY, "eu")
    assert p.region == "ru"
    assert "YANDEX_TTS_REGION" in capfd.readouterr().err
    _run(p, "x", tmp_path / "o.mp3")
    assert str(seen[0].url) == yandex_mod.REGION_URLS["ru"]


def test_empty_role_is_not_sent(monkeypatch, tmp_path):
    seen = _install(monkeypatch, _ok)
    _run(yandex_mod.YandexTTSProvider(VOICE, "  ", 1.0, KEY), "x", tmp_path / "o.mp3")
    assert json.loads(seen[0].content)["hints"] == [{"voice": "masha"}, {"speed": 1.0}]


@pytest.mark.parametrize("sep", ["", "\n", "\r\n", "\n\n  "])
def test_stream_framing_variants(monkeypatch, tmp_path, sep):
    # The REST gateway streams concatenated JSON objects; don't depend on exact separators.
    _install(monkeypatch, lambda r: httpx.Response(200, text=_stream(_chunk(PART_1), sep=sep)))
    out = tmp_path / "o.mp3"
    _run(yandex_mod.YandexTTSProvider(VOICE, "", 1.0, KEY), "x", out)
    assert out.read_bytes() == PART_1


def test_pretty_printed_stream_is_parsed(monkeypatch, tmp_path):
    body = "\n".join(json.dumps(m, indent=2) for m in (_chunk(PART_1), _chunk(PART_2)))
    _install(monkeypatch, lambda r: httpx.Response(200, text=body))
    out = tmp_path / "o.mp3"
    _run(yandex_mod.YandexTTSProvider(VOICE, "", 1.0, KEY), "x", out)
    assert out.read_bytes() == PART_1 + PART_2


def test_messages_without_audio_are_skipped(monkeypatch, tmp_path):
    text_only = {"result": {"textChunk": {"text": "x"}}}
    _install(
        monkeypatch,
        lambda r: httpx.Response(200, text=_stream(text_only, _chunk(PART_1), text_only)),
    )
    out = tmp_path / "o.mp3"
    _run(yandex_mod.YandexTTSProvider(VOICE, "", 1.0, KEY), "x", out)
    assert out.read_bytes() == PART_1


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: httpx.Response(401, json={"error": {"message": "Unauthenticated"}}),
        lambda r: httpx.Response(429, text="quota"),
        lambda r: httpx.Response(500, text="boom"),
        lambda r: httpx.Response(200, text="not json"),
        lambda r: httpx.Response(200, text=""),
        lambda r: httpx.Response(200, text=_stream({"nope": 1})),
        lambda r: httpx.Response(200, text=_stream(["list"])),
        lambda r: httpx.Response(
            200, text=_stream({"result": {"audioChunk": {"data": "@@@not-base64@@@"}}})
        ),
        # An error can arrive mid-stream after a 200 with some audio already sent.
        lambda r: httpx.Response(
            200, text=_stream(_chunk(PART_1), {"error": {"message": "role not supported"}})
        ),
        lambda r: httpx.Response(200, text=_stream(_chunk(PART_1)) + '{"result": '),
    ],
)
def test_errors_raise_provider_error_and_write_nothing(monkeypatch, tmp_path, handler):
    _install(monkeypatch, handler)
    out = tmp_path / "o.mp3"
    with pytest.raises(ProviderError) as exc:
        _run(yandex_mod.YandexTTSProvider(VOICE, "", 1.0, KEY), "текст", out)
    assert KEY not in str(exc.value)
    assert not out.exists()


def test_stream_error_message_is_surfaced(monkeypatch, tmp_path):
    body = _stream({"error": {"grpcCode": 3, "message": "Unsupported role: evil"}})
    _install(monkeypatch, lambda r: httpx.Response(200, text=body))
    with pytest.raises(ProviderError, match="Unsupported role: evil"):
        _run(yandex_mod.YandexTTSProvider(VOICE, "evil", 1.0, KEY), "x", tmp_path / "o.mp3")


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
        _run(yandex_mod.YandexTTSProvider(VOICE, "", 1.0, KEY), "x", tmp_path / "o.mp3")
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
    with pytest.raises(ProviderError, match="network") as exc:
        _run(yandex_mod.YandexTTSProvider(VOICE, "", 1.0, KEY), "x", tmp_path / "o.mp3")
    assert exc.value.billed is billed


def test_http_error_message_is_truncated(monkeypatch, tmp_path):
    _install(monkeypatch, lambda r: httpx.Response(500, text="x" * 1000))
    with pytest.raises(ProviderError) as exc:
        _run(yandex_mod.YandexTTSProvider(VOICE, "", 1.0, KEY), "x", tmp_path / "o.mp3")
    assert "HTTP 500" in str(exc.value)
    assert len(str(exc.value)) < 260


def test_missing_key_raises_without_network(monkeypatch, tmp_path):
    seen = _install(monkeypatch, _ok)
    with pytest.raises(ProviderError, match="tts yandex-key") as exc:
        _run(yandex_mod.YandexTTSProvider(VOICE, "", 1.0, None), "x", tmp_path / "o.mp3")
    assert exc.value.billed is False
    assert seen == []


def test_speed_clamped_and_logged(capfd):
    assert yandex_mod.YandexTTSProvider(VOICE, "", 5.0, KEY).speed == 3.0
    assert yandex_mod.YandexTTSProvider(VOICE, "", 0.01, KEY).speed == 0.1
    err = capfd.readouterr().err
    assert "YANDEX_TTS_SPEED" in err and "clamped" in err


def test_describe_never_contains_key():
    d = yandex_mod.YandexTTSProvider(VOICE, "good", 1.0, KEY, "kz").describe()
    assert d == {
        "provider": "yandex",
        "region": "kz",
        "voice": VOICE,
        "role": "good",
        "speed": 1.0,
        "key_loaded": True,
    }
    assert KEY not in json.dumps(d)


def test_describe_shows_unset_role_as_none():
    assert yandex_mod.YandexTTSProvider(VOICE, "", 1.0, None).describe()["role"] is None
