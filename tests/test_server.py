"""
Server tests — mock providers (edge-tts, openai, google or yandex) and afplay so tests don't
need network or audio output. A fresh ThreadingHTTPServer is spun per-test on
a random port to avoid interference with the live launchd-managed server on
7852.
"""

import importlib
import json
import socket
import threading
import time
from http.client import HTTPConnection
from pathlib import Path

import pytest


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _FakeEdgeCommunicate:
    def __init__(self, *a, **kw):
        pass

    async def save(self, path):
        Path(path).write_bytes(b"\x00")


def _patch_edge(monkeypatch):
    import claude_code_tts.providers.edge as edge_mod

    monkeypatch.setattr(edge_mod.edge_tts, "Communicate", _FakeEdgeCommunicate)


def _patch_afplay(monkeypatch, srv):
    _orig_popen = srv.subprocess.Popen

    def _fake_popen(args, **kw):
        if args and args[0] == "afplay":
            args = ["true"]
        return _orig_popen(args, **kw)

    monkeypatch.setattr(srv.subprocess, "Popen", _fake_popen)


@pytest.fixture
def server(monkeypatch, tmp_path):
    """Fresh server with edge-tts provider (the default) + afplay stubbed out."""
    port = _free_port()
    lock_file = tmp_path / "nl-recording.lock"

    monkeypatch.setenv("TTS_PORT", str(port))
    monkeypatch.setenv("TTS_LOCK_FILE", str(lock_file))
    monkeypatch.setenv("TTS_RESPECT_LOCK", "1")
    monkeypatch.delenv("TTS_PROVIDER", raising=False)
    # Existing server-level tests predate the TL;DR gate; disable it so they
    # exercise the full speak path. Dedicated tldr_server fixture below re-enables it.
    monkeypatch.setenv("TTS_TLDR_ONLY", "0")

    import claude_code_tts.server as srv

    importlib.reload(srv)

    _patch_edge(monkeypatch)
    _patch_afplay(monkeypatch, srv)

    httpd = srv.ThreadingHTTPServer((srv.HOST, port), srv.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    try:
        yield {"port": port, "lock_file": lock_file, "srv": srv}
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def openai_server(monkeypatch, tmp_path):
    """Fresh server with provider=openai, mocked AsyncOpenAI, fake Keychain key."""
    port = _free_port()
    lock_file = tmp_path / "nl-recording.lock"

    monkeypatch.setenv("TTS_PORT", str(port))
    monkeypatch.setenv("TTS_LOCK_FILE", str(lock_file))
    monkeypatch.setenv("TTS_RESPECT_LOCK", "1")
    monkeypatch.setenv("TTS_PROVIDER", "openai")
    # This fixture tests the openai speak path; bypass the TL;DR gate so
    # payloads without a TL;DR block still reach the provider.
    monkeypatch.setenv("TTS_TLDR_ONLY", "0")

    import claude_code_tts.keychain as keychain_mod

    monkeypatch.setattr(keychain_mod, "get_openai_key", lambda: "sk-test-dummy")

    # Stub AsyncOpenAI on the providers.openai module before server reload uses it.
    import claude_code_tts.providers.openai as openai_provider

    class _FakeStreamingResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def stream_to_file(self, path):
            Path(path).write_bytes(b"\x00")

    class _FakeStreamCtx:
        def create(self, **kw):
            return _FakeStreamingResponse()

    class _FakeSpeech:
        def __init__(self):
            self.with_streaming_response = _FakeStreamCtx()

    class _FakeAudio:
        def __init__(self):
            self.speech = _FakeSpeech()

    class _FakeAsyncOpenAI:
        def __init__(self, *a, **kw):
            self.audio = _FakeAudio()

    monkeypatch.setattr(openai_provider, "AsyncOpenAI", _FakeAsyncOpenAI)

    import claude_code_tts.server as srv

    importlib.reload(srv)
    _patch_afplay(monkeypatch, srv)

    httpd = srv.ThreadingHTTPServer((srv.HOST, port), srv.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    try:
        yield {"port": port, "srv": srv}
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def openai_server_no_key(monkeypatch, tmp_path):
    """Provider=openai but Keychain has no key — synthesize must fail cleanly."""
    port = _free_port()
    lock_file = tmp_path / "nl-recording.lock"

    monkeypatch.setenv("TTS_PORT", str(port))
    monkeypatch.setenv("TTS_LOCK_FILE", str(lock_file))
    monkeypatch.setenv("TTS_RESPECT_LOCK", "1")
    monkeypatch.setenv("TTS_PROVIDER", "openai")
    # This fixture tests the openai speak path; bypass the TL;DR gate so
    # payloads without a TL;DR block still reach the provider.
    monkeypatch.setenv("TTS_TLDR_ONLY", "0")

    import claude_code_tts.keychain as keychain_mod

    monkeypatch.setattr(keychain_mod, "get_openai_key", lambda: None)

    import claude_code_tts.server as srv

    importlib.reload(srv)
    _patch_afplay(monkeypatch, srv)

    httpd = srv.ThreadingHTTPServer((srv.HOST, port), srv.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    try:
        yield {"port": port, "srv": srv}
    finally:
        httpd.shutdown()
        httpd.server_close()


def _request(port: int, method: str, path: str, body: dict | None = None) -> tuple[int, bytes]:
    conn = HTTPConnection("127.0.0.1", port, timeout=3)
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=headers)
    resp = conn.getresponse()
    payload = resp.read()
    conn.close()
    return resp.status, payload


def test_health_endpoint(server):
    status, body = _request(server["port"], "GET", "/health")
    assert status == 200
    data = json.loads(body)
    assert data["ok"] is True
    assert data["config"]["provider"] == "edge-tts"
    assert "voice" in data["config"]
    assert data["locked"] is False
    # Main fixture disables TLDR-only; verify the flag is reflected in /health
    # so `tts doctor`/`status` can observe either mode symmetrically.
    assert data["tldr_only"] is False


def test_speak_with_mocked_edge_tts(server):
    status, body = _request(server["port"], "POST", "/speak", {"text": "Привет"})
    assert status == 202
    assert json.loads(body)["queued"] is True
    time.sleep(0.1)


def test_speak_locked_returns_423(server):
    server["lock_file"].touch()
    try:
        status, body = _request(server["port"], "POST", "/speak", {"text": "Прячь"})
        assert status == 423
        assert "recording" in json.loads(body)["error"].lower()
    finally:
        server["lock_file"].unlink(missing_ok=True)


def test_empty_after_clean_returns_204(server):
    # Code-block-only input collapses to empty after clean_for_tts.
    status, body = _request(server["port"], "POST", "/speak", {"text": "```code only```"})
    assert status == 204
    assert body == b""


def test_missing_text_returns_400(server):
    status, body = _request(server["port"], "POST", "/speak", {"not_text": "x"})
    assert status == 400


def test_stop_endpoint(server):
    status, _ = _request(server["port"], "POST", "/stop")
    assert status == 200


def test_invalid_json_returns_400(server):
    conn = HTTPConnection("127.0.0.1", server["port"], timeout=3)
    conn.request(
        "POST",
        "/speak",
        body=b"{not json",
        headers={"Content-Type": "application/json", "Content-Length": "9"},
    )
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    assert resp.status == 400
    assert "invalid" in json.loads(body)["error"].lower()


def test_empty_body_returns_400(server):
    status, body = _request(server["port"], "POST", "/speak")
    assert status == 400


def test_unknown_path_returns_404(server):
    status, _ = _request(server["port"], "GET", "/nope")
    assert status == 404


def test_openai_provider_health(openai_server):
    status, body = _request(openai_server["port"], "GET", "/health")
    assert status == 200
    data = json.loads(body)
    cfg = data["config"]
    assert cfg["provider"] == "openai"
    assert cfg["model"] == "gpt-4o-mini-tts"
    assert cfg["voice"] == "nova"
    assert cfg["key_loaded"] is True
    # Key itself must never leak into /health.
    assert "api_key" not in cfg
    assert "sk-" not in body.decode()


def test_openai_provider_speak_ok(openai_server):
    status, body = _request(openai_server["port"], "POST", "/speak", {"text": "Привет"})
    assert status == 202
    assert json.loads(body)["queued"] is True
    time.sleep(0.1)


def test_openai_provider_missing_key_returns_502(openai_server_no_key):
    status, body = _request(openai_server_no_key["port"], "POST", "/speak", {"text": "Привет"})
    assert status == 502
    assert "key" in json.loads(body)["error"].lower()


def test_unknown_provider_falls_back_to_edge(monkeypatch):
    """Typo in TTS_PROVIDER must not brick the server — fall back to edge-tts."""
    monkeypatch.setenv("TTS_PROVIDER", "garbage")
    monkeypatch.setenv("TTS_PORT", str(_free_port()))

    import claude_code_tts.server as srv

    importlib.reload(srv)

    assert srv.PROVIDER.name == "edge-tts"
    assert srv.PROVIDER._alerter is not None


def test_google_fallback_edge_shares_the_outage_alerter(monkeypatch):
    """edge-tts as Google's fallback must diagnose outages like the default edge-tts."""
    monkeypatch.setenv("TTS_PROVIDER", "google")
    monkeypatch.setenv("TTS_PORT", str(_free_port()))

    import claude_code_tts.keychain as keychain_mod

    monkeypatch.setattr(keychain_mod, "get_google_key", lambda: "AIza-test-dummy")

    import claude_code_tts.server as srv

    importlib.reload(srv)

    assert srv.PROVIDER._fallback._alerter is not None


def test_invalid_speed_does_not_crash_server(monkeypatch):
    """A malformed OPENAI_TTS_SPEED env must be tolerated; server uses 1.0."""
    monkeypatch.setenv("OPENAI_TTS_SPEED", "not-a-number")
    monkeypatch.setenv("TTS_PORT", str(_free_port()))
    monkeypatch.delenv("TTS_PROVIDER", raising=False)

    import claude_code_tts.server as srv

    importlib.reload(srv)

    assert srv.OPENAI_SPEED == 1.0


@pytest.fixture
def google_server(monkeypatch, tmp_path):
    """Fresh server with provider=google: fake key, httpx mocked, edge/afplay/notify stubbed."""
    import base64

    import httpx

    port = _free_port()
    lock_file = tmp_path / "nl-recording.lock"
    usage_file = tmp_path / "google-usage.json"

    monkeypatch.setenv("TTS_PORT", str(port))
    monkeypatch.setenv("TTS_LOCK_FILE", str(lock_file))
    monkeypatch.setenv("TTS_RESPECT_LOCK", "1")
    monkeypatch.setenv("TTS_PROVIDER", "google")
    monkeypatch.setenv("TTS_TLDR_ONLY", "0")
    monkeypatch.setenv("GOOGLE_TTS_USAGE_FILE", str(usage_file))
    monkeypatch.delenv("GOOGLE_TTS_MONTHLY_CHAR_LIMIT", raising=False)
    monkeypatch.delenv("GOOGLE_TTS_SPEED", raising=False)

    import claude_code_tts.keychain as keychain_mod
    import claude_code_tts.notify as notify_mod
    import claude_code_tts.providers.google as google_mod

    monkeypatch.setattr(keychain_mod, "get_google_key", lambda: "AIza-test-dummy")
    notes: list[tuple[str, str]] = []
    monkeypatch.setattr(notify_mod, "send", lambda title, msg: notes.append((title, msg)))

    state = {"status": 200}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["status"] != 200:
            return httpx.Response(state["status"], text="boom")
        return httpx.Response(200, json={"audioContent": base64.b64encode(b"\x00").decode()})

    real_client = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real_client(*a, **kw)

    monkeypatch.setattr(google_mod.httpx, "AsyncClient", factory)

    import claude_code_tts.server as srv

    importlib.reload(srv)
    _patch_edge(monkeypatch)
    _patch_afplay(monkeypatch, srv)

    httpd = srv.ThreadingHTTPServer((srv.HOST, port), srv.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    try:
        yield {
            "port": port,
            "srv": srv,
            "lock_file": lock_file,
            "usage_file": usage_file,
            "state": state,
            "notes": notes,
        }
    finally:
        httpd.shutdown()
        httpd.server_close()


def _used(usage_file: Path) -> int:
    return json.loads(usage_file.read_text())["chars"] if usage_file.exists() else 0


def test_google_health_has_usage_and_no_key(google_server):
    status, body = _request(google_server["port"], "GET", "/health")
    assert status == 200
    cfg = json.loads(body)["config"]
    assert cfg["provider"] == "google"
    assert cfg["key_loaded"] is True
    assert cfg["fallback"] == "edge-tts"
    assert cfg["usage"]["limit"] == 990_000
    assert cfg["last_fallback"] is None
    assert "AIza" not in body.decode()


def test_google_speak_counts_usage(google_server):
    status, body = _request(google_server["port"], "POST", "/speak", {"text": "Привет"})
    assert status == 202
    assert _used(google_server["usage_file"]) == len("Привет")
    _, health = _request(google_server["port"], "GET", "/health")
    assert json.loads(health)["config"]["usage"]["used"] == len("Привет")


def test_google_failure_still_speaks_via_edge(google_server):
    google_server["state"]["status"] = 500
    status, _ = _request(google_server["port"], "POST", "/speak", {"text": "Привет"})
    assert status == 202
    _, health = _request(google_server["port"], "GET", "/health")
    cfg = json.loads(health)["config"]
    assert cfg["fallback_count"] == 1
    assert "HTTP 500" in cfg["last_fallback"]["reason"]
    assert google_server["notes"] == []


def test_google_locked_returns_423_and_counts_nothing(google_server):
    google_server["lock_file"].touch()
    try:
        status, _ = _request(google_server["port"], "POST", "/speak", {"text": "Прячь"})
    finally:
        google_server["lock_file"].unlink(missing_ok=True)
    assert status == 423
    assert _used(google_server["usage_file"]) == 0
    assert google_server["notes"] == []


def test_google_limit_exhaustion_switches_to_edge(google_server):
    from datetime import UTC, datetime

    from claude_code_tts.usage import month_key

    google_server["usage_file"].write_text(
        json.dumps(
            {
                "version": 1,
                "month": month_key(datetime.now(UTC)),
                "chars": 989_995,
                "flags": ["warn_80", "warn_90"],
            }
        )
    )
    status, _ = _request(google_server["port"], "POST", "/speak", {"text": "Привет"})
    assert status == 202
    assert _used(google_server["usage_file"]) == 989_995
    assert len(google_server["notes"]) == 1
    _, health = _request(google_server["port"], "GET", "/health")
    assert "exhausted" in json.loads(health)["config"]["usage"]["flags"]


def test_google_bad_limit_and_speed_env_do_not_crash(monkeypatch, tmp_path):
    monkeypatch.setenv("TTS_PORT", str(_free_port()))
    monkeypatch.setenv("TTS_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_TTS_USAGE_FILE", str(tmp_path / "u.json"))
    monkeypatch.setenv("GOOGLE_TTS_MONTHLY_CHAR_LIMIT", "lots")
    monkeypatch.setenv("GOOGLE_TTS_SPEED", "fast")

    import claude_code_tts.keychain as keychain_mod

    monkeypatch.setattr(keychain_mod, "get_google_key", lambda: None)

    import claude_code_tts.server as srv

    importlib.reload(srv)

    assert srv.GOOGLE_LIMIT == 990_000
    assert srv.GOOGLE_SPEED == 1.0
    assert srv.PROVIDER.name == "google"


@pytest.fixture
def tldr_server(monkeypatch, tmp_path):
    """Server with TTS_TLDR_ONLY=1 (the default in production) + edge-tts mocked."""
    port = _free_port()
    lock_file = tmp_path / "nl-recording.lock"

    monkeypatch.setenv("TTS_PORT", str(port))
    monkeypatch.setenv("TTS_LOCK_FILE", str(lock_file))
    monkeypatch.setenv("TTS_RESPECT_LOCK", "1")
    monkeypatch.setenv("TTS_TLDR_ONLY", "1")
    monkeypatch.delenv("TTS_PROVIDER", raising=False)
    # Guarantee the silent-skip path isn't masked by an inherited fallback env.
    monkeypatch.delenv("TTS_TLDR_FALLBACK_TEXT", raising=False)

    import claude_code_tts.server as srv

    importlib.reload(srv)

    _patch_edge(monkeypatch)
    _patch_afplay(monkeypatch, srv)

    httpd = srv.ThreadingHTTPServer((srv.HOST, port), srv.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    try:
        yield {"port": port, "srv": srv}
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_tldr_only_health_reports_flag(tldr_server):
    status, body = _request(tldr_server["port"], "GET", "/health")
    assert status == 200
    assert json.loads(body)["tldr_only"] is True


def test_tldr_only_speaks_when_marker_present(tldr_server):
    msg = "Длинное вступление.\n\n## TL;DR\n\nКратко: готово."
    status, body = _request(tldr_server["port"], "POST", "/speak", {"text": msg})
    assert status == 202
    assert "готово" in json.loads(body)["preview"].lower()


def test_tldr_only_silent_without_marker(tldr_server):
    status, body = _request(
        tldr_server["port"],
        "POST",
        "/speak",
        {"text": "Большое сообщение без итогового блока."},
    )
    assert status == 204
    assert body == b""


def test_tldr_only_skip_logs_preview(tldr_server, capfd):
    """On TL;DR-only skip the server must log a preview of the rejected text
    so users can tell which message was dropped (observability gap from review)."""
    msg = "Это начало отброшенного сообщения без итогового блока — продолжение тут."
    status, _ = _request(tldr_server["port"], "POST", "/speak", {"text": msg})
    assert status == 204
    # log() writes to stderr with a '[tts]' prefix; capfd captures even when
    # afplay is stubbed. Preview must mention 'skipping' and include the first
    # chars of the rejected message.
    err = capfd.readouterr().err
    assert "skipping playback" in err
    assert "Это начало" in err


# ---------------------------------------------------------------------------
# TLDR fallback text (TTS_TLDR_FALLBACK_TEXT)
# ---------------------------------------------------------------------------

FALLBACK_PHRASE = "Задача выполнена. Нет блока TL;DR"


@pytest.fixture
def tldr_fallback_server(monkeypatch, tmp_path):
    """TLDR_ONLY=1 + a configured fallback phrase. Edge-tts + afplay mocked."""
    port = _free_port()
    lock_file = tmp_path / "nl-recording.lock"

    monkeypatch.setenv("TTS_PORT", str(port))
    monkeypatch.setenv("TTS_LOCK_FILE", str(lock_file))
    monkeypatch.setenv("TTS_RESPECT_LOCK", "1")
    monkeypatch.setenv("TTS_TLDR_ONLY", "1")
    monkeypatch.setenv("TTS_TLDR_FALLBACK_TEXT", FALLBACK_PHRASE)
    monkeypatch.delenv("TTS_PROVIDER", raising=False)

    import claude_code_tts.server as srv

    importlib.reload(srv)
    _patch_edge(monkeypatch)
    _patch_afplay(monkeypatch, srv)

    httpd = srv.ThreadingHTTPServer((srv.HOST, port), srv.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    try:
        yield {"port": port, "srv": srv}
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_tldr_fallback_health_reports_flag(tldr_fallback_server):
    status, body = _request(tldr_fallback_server["port"], "GET", "/health")
    assert status == 200
    data = json.loads(body)
    assert data["tldr_only"] is True
    assert data["tldr_fallback"] is True


def test_tldr_fallback_spoken_when_marker_missing(tldr_fallback_server):
    """No TL;DR + fallback configured → speak fallback, not silent 204."""
    status, body = _request(
        tldr_fallback_server["port"],
        "POST",
        "/speak",
        {"text": "Сообщение без итогового блока."},
    )
    assert status == 202
    preview = json.loads(body)["preview"]
    assert "задача выполнена" in preview.lower()


def test_tldr_fallback_does_not_override_existing_tldr(tldr_fallback_server):
    """When TL;DR is present, the fallback must NOT replace it."""
    msg = "Вступление.\n\n## TL;DR\n\nКратко: готово."
    status, body = _request(tldr_fallback_server["port"], "POST", "/speak", {"text": msg})
    assert status == 202
    preview = json.loads(body)["preview"].lower()
    assert "готово" in preview
    assert "задача выполнена" not in preview


def test_tldr_fallback_log_mentions_fallback(tldr_fallback_server, capfd):
    _request(
        tldr_fallback_server["port"],
        "POST",
        "/speak",
        {"text": "Нет блока итога тут."},
    )
    err = capfd.readouterr().err
    assert "fallback" in err.lower()
    # Silent-skip wording must NOT appear — that's the path we just replaced.
    assert "skipping playback" not in err


def test_tldr_fallback_empty_env_keeps_silent_skip(monkeypatch, tmp_path):
    """Explicit empty TTS_TLDR_FALLBACK_TEXT behaves identically to unset."""
    port = _free_port()
    monkeypatch.setenv("TTS_PORT", str(port))
    monkeypatch.setenv("TTS_LOCK_FILE", str(tmp_path / "lock"))
    monkeypatch.setenv("TTS_TLDR_ONLY", "1")
    monkeypatch.setenv("TTS_TLDR_FALLBACK_TEXT", "")
    monkeypatch.delenv("TTS_PROVIDER", raising=False)

    import claude_code_tts.server as srv

    importlib.reload(srv)
    _patch_edge(monkeypatch)
    _patch_afplay(monkeypatch, srv)

    httpd = srv.ThreadingHTTPServer((srv.HOST, port), srv.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, _ = _request(port, "POST", "/speak", {"text": "Нет TL;DR."})
        assert status == 204
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def yandex_server(monkeypatch, tmp_path):
    """Fresh server with provider=yandex: key and httpx faked, afplay stubbed."""
    import base64

    import httpx

    port = _free_port()
    monkeypatch.setenv("TTS_PORT", str(port))
    monkeypatch.setenv("TTS_LOCK_FILE", str(tmp_path / "nl-recording.lock"))
    monkeypatch.setenv("TTS_PROVIDER", "yandex")
    monkeypatch.setenv("TTS_TLDR_ONLY", "0")
    monkeypatch.setenv("YANDEX_TTS_VOICE", "dasha")
    monkeypatch.setenv("YANDEX_TTS_ROLE", "friendly")
    monkeypatch.setenv("YANDEX_TTS_REGION", "kz")
    monkeypatch.setenv("YANDEX_TTS_PITCH", "-120")
    monkeypatch.delenv("YANDEX_TTS_SPEED", raising=False)

    import claude_code_tts.keychain as keychain_mod
    import claude_code_tts.providers.yandex as yandex_mod

    state = {"status": 200, "key": "AQVN-test-dummy"}
    monkeypatch.setattr(keychain_mod, "get_yandex_key", lambda: state["key"])

    def handler(request: httpx.Request) -> httpx.Response:
        if state["status"] != 200:
            return httpx.Response(state["status"], text="boom")
        chunk = {"result": {"audioChunk": {"data": base64.b64encode(b"\x00").decode()}}}
        return httpx.Response(200, text=json.dumps(chunk))

    real_client = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real_client(*a, **kw)

    monkeypatch.setattr(yandex_mod.httpx, "AsyncClient", factory)

    def start():
        import claude_code_tts.server as srv

        importlib.reload(srv)
        _patch_afplay(monkeypatch, srv)
        httpd = srv.ThreadingHTTPServer((srv.HOST, port), srv.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return srv

    servers: list = []
    try:
        yield {"port": port, "state": state, "start": start}
    finally:
        for httpd in servers:
            httpd.shutdown()
            httpd.server_close()


def test_yandex_health_reports_config_without_key(yandex_server):
    yandex_server["start"]()
    status, body = _request(yandex_server["port"], "GET", "/health")
    assert status == 200
    cfg = json.loads(body)["config"]
    assert cfg == {
        "provider": "yandex",
        "region": "kz",
        "voice": "dasha",
        "role": "friendly",
        "speed": 1.0,
        "pitch": -120.0,
        "key_loaded": True,
    }
    assert "AQVN" not in body.decode()


def test_yandex_speak_ok(yandex_server):
    yandex_server["start"]()
    status, body = _request(yandex_server["port"], "POST", "/speak", {"text": "Привет"})
    assert status == 202
    assert json.loads(body)["queued"] is True


def test_yandex_failure_returns_502_without_fallback(yandex_server):
    yandex_server["state"]["status"] = 401
    yandex_server["start"]()
    status, body = _request(yandex_server["port"], "POST", "/speak", {"text": "Привет"})
    assert status == 502
    assert "HTTP 401" in json.loads(body)["error"]


def test_yandex_missing_key_returns_502(yandex_server):
    yandex_server["state"]["key"] = None
    yandex_server["start"]()
    status, body = _request(yandex_server["port"], "POST", "/speak", {"text": "Привет"})
    assert status == 502
    assert "tts yandex-key" in json.loads(body)["error"]


def test_invalid_yandex_pitch_does_not_crash_server(monkeypatch):
    """A malformed YANDEX_TTS_PITCH must not brick the server; it falls back to 0."""
    monkeypatch.setenv("YANDEX_TTS_PITCH", "high")
    monkeypatch.setenv("TTS_PORT", str(_free_port()))
    monkeypatch.delenv("TTS_PROVIDER", raising=False)

    import claude_code_tts.server as srv

    importlib.reload(srv)

    assert srv.YANDEX_PITCH == 0.0
