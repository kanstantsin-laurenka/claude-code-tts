"""
TTS PROVIDERS

Pluggable synthesis backends: edge-tts (default, free) and OpenAI (paid, key
from Keychain). The server picks one at startup based on TTS_PROVIDER.

Business rules:
  - Providers are constructed once at module load and re-created only on a
    full launchd restart (same lifecycle as the rest of server config).
  - synthesize() must write an MP3 to out_path or raise ProviderError. The
    server maps ProviderError to HTTP 502; Claude Code never blocks on TTS.
  - describe() feeds /health — it must never include secrets.
"""

from abc import ABC, abstractmethod


class ProviderError(RuntimeError):
    """Raised by synthesize() on any backend failure (network, auth, quota, …)."""


class TTSProvider(ABC):
    name: str

    @abstractmethod
    async def synthesize(self, text: str, out_path: str) -> None:
        """Write synthesized MP3 to out_path. Raises ProviderError on failure."""

    @abstractmethod
    def describe(self) -> dict:
        """Return non-secret config fields for /health."""
