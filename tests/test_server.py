"""
Server tests — mock edge_tts.Communicate and afplay so tests don't need network
or audio output. A fresh ThreadingHTTPServer is spun per-test on a random port
to avoid interference with the live launchd-managed server on 7852.
"""
import importlib
import json
import os
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


@pytest.fixture
def server(monkeypatch, tmp_path):
    """Spin up a fresh server module with edge_tts + afplay stubbed out."""
    port = _free_port()
    lock_file = tmp_path / "nl-recording.lock"

    monkeypatch.setenv("TTS_PORT", str(port))
    monkeypatch.setenv("TTS_LOCK_FILE", str(lock_file))
    monkeypatch.setenv("TTS_RESPECT_LOCK", "1")

    # Ensure fresh module read picks up env vars.
    import claude_code_tts.server as srv
    importlib.reload(srv)

    class FakeCommunicate:
        def __init__(self, *a, **kw): pass
        async def save(self, path):
            Path(path).write_bytes(b"\x00")  # empty MP3 placeholder

    monkeypatch.setattr(srv.edge_tts, "Communicate", FakeCommunicate)

    # afplay → `true` so we don't make noise and Popen exits immediately.
    _orig_popen = srv.subprocess.Popen
    def _fake_popen(args, **kw):
        if args and args[0] == "afplay":
            args = ["true"]
        return _orig_popen(args, **kw)
    monkeypatch.setattr(srv.subprocess, "Popen", _fake_popen)

    httpd = srv.ThreadingHTTPServer((srv.HOST, port), srv.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    try:
        yield {"port": port, "lock_file": lock_file, "srv": srv}
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
    assert "voice" in data
    assert data["locked"] is False


def test_speak_with_mocked_edge_tts(server):
    status, body = _request(server["port"], "POST", "/speak", {"text": "Привет"})
    assert status == 202
    assert json.loads(body)["queued"] is True
    # give fake popen a moment to exit
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
