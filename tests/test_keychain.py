"""
Keychain tests — subprocess.run is faked so CI (Linux) never needs `security`.
"""

import subprocess

import claude_code_tts.keychain as keychain


def _fake_run(returncode: int, stdout: str, calls: list):
    def run(argv, **kw):
        calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")

    return run


def test_get_key_argv_and_strip(monkeypatch):
    calls: list = []
    monkeypatch.setattr(keychain.subprocess, "run", _fake_run(0, "  AIzaSecret\n", calls))
    assert keychain.get_key("google") == "AIzaSecret"
    argv, kw = calls[0]
    assert argv == [
        "security",
        "find-generic-password",
        "-s",
        "claude-code-tts",
        "-a",
        "google",
        "-w",
    ]
    assert kw["capture_output"] is True


def test_get_key_none_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(keychain.subprocess, "run", _fake_run(44, "", []))
    assert keychain.get_key("google") is None


def test_get_key_none_on_empty_output(monkeypatch):
    monkeypatch.setattr(keychain.subprocess, "run", _fake_run(0, "\n", []))
    assert keychain.get_key("google") is None


def test_wrappers_delegate(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(keychain, "get_key", lambda account: seen.append(account) or "k")
    assert keychain.get_openai_key() == "k"
    assert keychain.get_google_key() == "k"
    assert keychain.get_yandex_key() == "k"
    assert seen == ["openai", "google", "yandex"]
