"""
GOOGLE CLOUD TTS PROVIDER (Chirp 3 HD)

Plain REST client for texttospeech.googleapis.com. Knows nothing about quotas or
fallbacks — that policy lives in google_fallback.py, which wraps this class.

Business rules:
  - API key goes in the X-Goog-Api-Key header, never in the URL (URLs end up in logs).
  - Input limit is 5000 UTF-8 bytes (Chirp 3 HD); Cyrillic is 2 bytes/char, so the check
    is on bytes, not characters. Exposed via unavailable_reason() so the wrapper can skip
    Google BEFORE counting quota.
  - speakingRate is clamped to Chirp 3 HD's 0.25–2.0 range. Pitch is unsupported by
    Chirp 3 HD, so there is no pitch knob.
  - The key never appears in error messages or describe().
  - ProviderError.billed is False only for failures that provably never reached billing
    (connect errors, 4xx); everything else stays "possibly billed".

Dependencies: httpx (pip).
"""

import base64
import binascii
import sys

import httpx

from . import ProviderError, TTSProvider

GOOGLE_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
SPEED_MIN = 0.25
SPEED_MAX = 2.0
MAX_INPUT_BYTES = 5000

# Failures raised before the request body could reach Google, so nothing was billed.
# Read/write timeouts are excluded on purpose: the request may already be processing.
_NEVER_SENT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


class GoogleTTSProvider(TTSProvider):
    name = "google"

    def __init__(self, voice: str, speed: float, api_key: str | None) -> None:
        self.voice = voice
        clamped = max(SPEED_MIN, min(SPEED_MAX, speed))
        if clamped != speed:
            print(
                f"[tts] GOOGLE_TTS_SPEED={speed} out of [{SPEED_MIN}, {SPEED_MAX}], "
                f"clamped to {clamped}",
                file=sys.stderr,
                flush=True,
            )
        self.speed = clamped
        self._api_key = api_key or None

    @property
    def has_key(self) -> bool:
        return self._api_key is not None

    @property
    def language_code(self) -> str:
        # "ru-RU-Chirp3-HD-Aoede" -> "ru-RU"
        return "-".join(self.voice.split("-")[:2])

    def unavailable_reason(self, text: str) -> str | None:
        if not self.has_key:
            return "no API key"
        if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
            return f"text > {MAX_INPUT_BYTES} bytes"
        return None

    async def synthesize(self, text: str, out_path: str) -> None:
        reason = self.unavailable_reason(text)
        if reason == "no API key":
            raise ProviderError("Google API key missing — set it with: tts google-key")
        if reason:
            raise ProviderError(f"Google TTS not usable: {reason}")

        body = {
            "input": {"text": text},
            "voice": {"languageCode": self.language_code, "name": self.voice},
            "audioConfig": {"audioEncoding": "MP3", "speakingRate": self.speed},
        }
        headers = {"X-Goog-Api-Key": self._api_key or ""}
        # AsyncClient is created per call: the server runs asyncio.run() per request, and a
        # client bound to a finished event loop breaks on reuse.
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as client:
                response = await client.post(GOOGLE_URL, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise ProviderError(
                f"Google TTS network error: {type(e).__name__}: {e}",
                billed=not isinstance(e, _NEVER_SENT_ERRORS),
            ) from e

        if response.status_code != 200:
            raise ProviderError(
                f"Google TTS HTTP {response.status_code}: {response.text[:200]}",
                # 4xx (bad key, bad request, 429) is rejected before synthesis; 5xx may have
                # been processed, so it stays "possibly billed".
                billed=not 400 <= response.status_code < 500,
            )

        try:
            audio = base64.b64decode(response.json()["audioContent"], validate=True)
        except (ValueError, KeyError, TypeError, binascii.Error) as e:
            raise ProviderError(f"Google TTS bad response: {type(e).__name__}: {e}") from e
        if not audio:
            raise ProviderError("Google TTS bad response: empty audioContent")

        # Written only after a successful decode so a failure never leaves a partial MP3.
        with open(out_path, "wb") as f:
            f.write(audio)

    def describe(self) -> dict:
        return {
            "provider": self.name,
            "voice": self.voice,
            "speed": self.speed,
            "key_loaded": self.has_key,
        }
