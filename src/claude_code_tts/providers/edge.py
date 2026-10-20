"""
EDGE-TTS PROVIDER

Wraps Microsoft edge-tts. Free, offline-ish (hits Microsoft's public endpoint),
supports rate/pitch. This is the default provider to preserve behavior for
everyone who already installed the tool before the provider split landed.

Business rules:
  - The endpoint is unofficial and Microsoft changes it from time to time; fixes ship as
    new edge-tts releases. So every failure is classified and the ProviderError names the
    installed edge-tts version, the cause and the error — the log alone tells what broke.
  - Only service failures (Microsoft rejected the request or changed the protocol) go to
    the optional alerter, which notifies the user. See providers/edge_alert.py.

Related: docs/CONFIGURATION.md#edge-tts-outages.
"""

import functools
from importlib import metadata
from typing import Literal

import aiohttp
import edge_tts
from edge_tts.exceptions import EdgeTTSException

from . import ProviderError, TTSProvider
from .edge_alert import EdgeOutageAlerter

EdgeErrorKind = Literal["service", "network", "config", "unknown"]

_KIND_LABELS: dict[EdgeErrorKind, str] = {
    "service": "Microsoft rejected the request",
    "network": "network problem",
    "config": "invalid voice/rate/pitch",
    "unknown": "unexpected error",
}


@functools.cache
def installed_edge_version() -> str:
    try:
        return metadata.version("edge-tts")
    except metadata.PackageNotFoundError:
        return "?"


def classify_edge_error(exc: BaseException) -> tuple[EdgeErrorKind, str]:
    """Return the failure kind and a short detail for logs and notifications."""
    # ⚠️ WARNING: order matters. WSServerHandshakeError (a ClientResponseError) is how a
    # 403 surfaces after edge-tts's own clock-skew retry; ServerTimeoutError is both a
    # network error and a TimeoutError. ClientConnectorError is also an OSError.
    if isinstance(exc, aiohttp.ClientResponseError):
        return "service", f"HTTP {exc.status}"
    if isinstance(exc, EdgeTTSException):
        return "service", type(exc).__name__
    if isinstance(exc, (aiohttp.ClientConnectionError, TimeoutError, OSError)):
        return "network", type(exc).__name__
    if isinstance(exc, (ValueError, TypeError)):
        return "config", type(exc).__name__
    return "unknown", type(exc).__name__


class EdgeTTSProvider(TTSProvider):
    name = "edge-tts"

    def __init__(
        self,
        voice: str,
        rate: str,
        pitch: str,
        alerter: EdgeOutageAlerter | None = None,
    ) -> None:
        self.voice = voice
        self.rate = rate
        self.pitch = pitch
        self._alerter = alerter

    async def synthesize(self, text: str, out_path: str) -> None:
        try:
            communicate = edge_tts.Communicate(text, self.voice, rate=self.rate, pitch=self.pitch)
            await communicate.save(out_path)
        except Exception as e:
            kind, detail = classify_edge_error(e)
            version = installed_edge_version()
            if kind == "service" and self._alerter is not None:
                self._alerter.report(version, detail)
            raise ProviderError(
                f"edge-tts {version} error: {_KIND_LABELS[kind]} ({detail}): {e}"
            ) from e

    def describe(self) -> dict:
        return {
            "provider": self.name,
            "voice": self.voice,
            "rate": self.rate,
            "pitch": self.pitch,
        }
