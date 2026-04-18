"""
KEYCHAIN

Thin wrappers around the macOS `security` CLI for storing the OpenAI API key.

Business rules:
  - Service name "claude-code-tts", account "openai" — both must match what
    bin/tts writes.
  - Never log or echo the key itself.
  - macOS-only is a project invariant (see CLAUDE.md).
"""

import subprocess

SERVICE = "claude-code-tts"
ACCOUNT_OPENAI = "openai"


def get_openai_key() -> str | None:
    """Return the stored OpenAI key or None if absent (non-zero exit)."""
    result = subprocess.run(
        ["security", "find-generic-password", "-s", SERVICE, "-a", ACCOUNT_OPENAI, "-w"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    key = result.stdout.strip()
    return key or None


def set_openai_key(key: str) -> None:
    """Store or update the OpenAI key. -U replaces an existing entry."""
    subprocess.run(
        [
            "security",
            "add-generic-password",
            "-U",
            "-s",
            SERVICE,
            "-a",
            ACCOUNT_OPENAI,
            "-w",
            key,
        ],
        check=True,
        capture_output=True,
    )


def delete_openai_key() -> bool:
    """Delete the stored OpenAI key. Returns True if it existed, False otherwise."""
    result = subprocess.run(
        ["security", "delete-generic-password", "-s", SERVICE, "-a", ACCOUNT_OPENAI],
        capture_output=True,
    )
    return result.returncode == 0
