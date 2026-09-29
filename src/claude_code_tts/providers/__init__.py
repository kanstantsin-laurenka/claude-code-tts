"""
TTS PROVIDERS

Pluggable synthesis backends: edge-tts (default, free), OpenAI (paid) and Google
Chirp 3 HD (free tier with a local monthly quota guard, wrapped by
google_fallback.GoogleWithFallback which falls back to edge-tts). API keys come
from the Keychain. The server picks one at startup based on TTS_PROVIDER.

Business rules:
  - Providers are constructed once at module load and re-created only on a
    full launchd restart (same lifecycle as the rest of server config).
  - synthesize() must write an MP3 to out_path or raise ProviderError. The
    server maps ProviderError to HTTP 502; Claude Code never blocks on TTS.
  - describe() feeds /health — it must never include secrets.
"""

from abc import ABC, abstractmethod


class ProviderError(RuntimeError):
    """Raised by synthesize() on any backend failure (network, auth, quota, …).

    `billed` says whether the backend may have charged for the request. It defaults to
    True (unknown = assume charged); providers set False only for failures that
    provably never reached billing, so quota-guarding wrappers can refund them.
    """

    def __init__(self, message: str, *, billed: bool = True) -> None:
        super().__init__(message)
        self.billed = billed


class TTSProvider(ABC):
    name: str

    @abstractmethod
    async def synthesize(self, text: str, out_path: str) -> None:
        """Write synthesized MP3 to out_path. Raises ProviderError on failure."""

    @abstractmethod
    def describe(self) -> dict:
        """Return non-secret config fields for /health."""
