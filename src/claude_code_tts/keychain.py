"""
KEYCHAIN

Read-only wrappers around the macOS `security` CLI for the OpenAI, Google and Yandex
API keys.

Business rules:
  - Service name "claude-code-tts"; accounts "openai", "google" and "yandex" — must
    match what bin/tts writes.
  - Writes are NOT done here: bin/tts runs `security add-generic-password ... -w` with
    no value so `security` prompts on the tty and the key never appears in argv/ps.
  - Never log or echo the key itself.
  - macOS-only is a project invariant (see CLAUDE.md).
"""

import subprocess

SERVICE = "claude-code-tts"
ACCOUNT_OPENAI = "openai"
ACCOUNT_GOOGLE = "google"
ACCOUNT_YANDEX = "yandex"


def get_key(account: str) -> str | None:
    """Return the stored key for `account` or None if absent (non-zero exit)."""
    # INVARIANT: capture_output=True — security echoes the value on error; dropping
    # this would leak the key into our stderr/logs.
    result = subprocess.run(
        ["security", "find-generic-password", "-s", SERVICE, "-a", account, "-w"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    key = result.stdout.strip()
    return key or None


# server.py calls these through the module attribute (keychain.get_*_key()) and tests
# monkeypatch them — keep them as separate module-level functions.
def get_openai_key() -> str | None:
    return get_key(ACCOUNT_OPENAI)


def get_google_key() -> str | None:
    return get_key(ACCOUNT_GOOGLE)


def get_yandex_key() -> str | None:
    return get_key(ACCOUNT_YANDEX)
