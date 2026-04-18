"""
Server tests — mock providers (edge-tts or openai) and afplay so tests don't
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
    assert cfg["model"] == "tts-1"
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


def test_invalid_speed_does_not_crash_server(monkeypatch):
    """A malformed OPENAI_TTS_SPEED env must be tolerated; server uses 1.0."""
    monkeypatch.setenv("OPENAI_TTS_SPEED", "not-a-number")
    monkeypatch.setenv("TTS_PORT", str(_free_port()))
    monkeypatch.delenv("TTS_PROVIDER", raising=False)

    import claude_code_tts.server as srv

    importlib.reload(srv)

    assert srv.OPENAI_SPEED == 1.0
