"""
YANDEX SPEECHKIT TTS PROVIDER (API v3, REST)

Plain REST client for SpeechKit v3 utteranceSynthesis. Paid service
with no free tier, so — like the openai provider — there is no quota guard and no
fallback: a failure surfaces as HTTP 502 and the Stop hook swallows it.

Business rules:
  - Yandex Cloud has two separate installations with separate accounts and IAM: Russia
    (console.yandex.cloud, *.api.cloud.yandex.net) and Kazakhstan (kz.console.yandex.cloud,
    *.api.yandexcloud.kz). A key only works against the installation that issued it, so
    the endpoint follows YANDEX_TTS_REGION; unknown values fall back to "ru" with a log.
  - Auth is a service-account API key in `Authorization: Api-Key …`. No x-folder-id:
    with a service account SpeechKit bills the folder the account lives in.
  - v3 over v1: half the price (0.1626 ₽ per started 250-char block vs 1342 ₽ per 1M
    chars) and the newer voices (masha, dasha, kirill, …) exist only in v3.
  - unsafeMode=True: without it v3 rejects text over 250 chars; with it SpeechKit splits
    the text itself and bills each 250-char block.
  - The response is a stream of JSON objects, each carrying a base64 MP3 chunk; chunks
    are concatenated in order. An {"error": …} object can arrive mid-stream after a 200,
    so the status code alone is not proof of success.
  - Empty role = the voice's default role; the hint is omitted, never sent empty.
  - speed is clamped to SpeechKit's 0.1–3.0 range; pitch (pitchShift, Hz) to −1000…1000.
    A zero pitch is omitted so requests stay identical to SpeechKit's own default.
  - The key never appears in error messages or describe().
  - ProviderError.billed is False only for failures that provably never reached billing
    (connect errors, 4xx); everything else stays "possibly billed".

Dependencies: httpx (pip).
Related: docs/CONFIGURATION.md#yandex-knobs
"""

import base64
import binascii
import json
import sys

import httpx

from . import ProviderError, TTSProvider

REGION_URLS = {
    "ru": "https://tts.api.cloud.yandex.net/tts/v3/utteranceSynthesis",
    "kz": "https://tts.api.yandexcloud.kz/tts/v3/utteranceSynthesis",
}
DEFAULT_REGION = "ru"
SPEED_MIN = 0.1
SPEED_MAX = 3.0
PITCH_MIN = -1000.0
PITCH_MAX = 1000.0

# Failures raised before the request body could reach Yandex, so nothing was billed.
# Read/write timeouts are excluded on purpose: the request may already be processing.
_NEVER_SENT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


def _iter_json_objects(body: str):
    """Yield each top-level JSON value from a stream of concatenated JSON objects.

    raw_decode instead of splitlines(): the gateway's separators (newline, none, or
    pretty-printed objects) are not documented, and this parses all of them.
    """
    decoder = json.JSONDecoder()
    pos = 0
    while True:
        while pos < len(body) and body[pos].isspace():
            pos += 1
        if pos == len(body):
            return
        value, pos = decoder.raw_decode(body, pos)
        yield value


class YandexTTSProvider(TTSProvider):
    name = "yandex"

    def __init__(
        self,
        voice: str,
        role: str,
        speed: float,
        api_key: str | None,
        region: str = DEFAULT_REGION,
        pitch: float = 0.0,
    ) -> None:
        if region not in REGION_URLS:
            print(
                f"[tts] unknown YANDEX_TTS_REGION={region!r}, using {DEFAULT_REGION!r}",
                file=sys.stderr,
                flush=True,
            )
            region = DEFAULT_REGION
        self.region = region
        self.voice = voice
        self.role = role.strip() or None
        clamped = max(SPEED_MIN, min(SPEED_MAX, speed))
        if clamped != speed:
            print(
                f"[tts] YANDEX_TTS_SPEED={speed} out of [{SPEED_MIN}, {SPEED_MAX}], "
                f"clamped to {clamped}",
                file=sys.stderr,
                flush=True,
            )
        self.speed = clamped
        clamped_pitch = max(PITCH_MIN, min(PITCH_MAX, pitch))
        if clamped_pitch != pitch:
            print(
                f"[tts] YANDEX_TTS_PITCH={pitch} out of [{PITCH_MIN}, {PITCH_MAX}], "
                f"clamped to {clamped_pitch}",
                file=sys.stderr,
                flush=True,
            )
        self.pitch = clamped_pitch
        self._api_key = api_key or None

    async def synthesize(self, text: str, out_path: str) -> None:
        if self._api_key is None:
            raise ProviderError(
                "Yandex API key missing — set it with: tts yandex-key", billed=False
            )

        hints: list[dict] = [{"voice": self.voice}]
        if self.role:
            hints.append({"role": self.role})
        hints.append({"speed": self.speed})
        if self.pitch:
            hints.append({"pitchShift": self.pitch})
        body = {
            "text": text,
            "hints": hints,
            "outputAudioSpec": {"containerAudio": {"containerAudioType": "MP3"}},
            "unsafeMode": True,
        }
        headers = {"Authorization": f"Api-Key {self._api_key}"}
        # AsyncClient is created per call: the server runs asyncio.run() per request, and a
        # client bound to a finished event loop breaks on reuse. The read timeout is longer
        # than Google's because the whole utterance is synthesized before the stream ends.
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=3.0)) as client:
                response = await client.post(REGION_URLS[self.region], json=body, headers=headers)
        except httpx.HTTPError as e:
            raise ProviderError(
                f"Yandex TTS network error: {type(e).__name__}: {e}",
                billed=not isinstance(e, _NEVER_SENT_ERRORS),
            ) from e

        if response.status_code != 200:
            raise ProviderError(
                f"Yandex TTS HTTP {response.status_code}: {response.text[:200]}",
                # 4xx (bad key, missing role, bad request) is rejected before synthesis;
                # 5xx may have been processed, so it stays "possibly billed".
                billed=not 400 <= response.status_code < 500,
            )

        audio = self._decode_stream(response.text)
        # Written only after the whole stream decoded so a failure never leaves a partial MP3.
        with open(out_path, "wb") as f:
            f.write(audio)

    @staticmethod
    def _decode_stream(body: str) -> bytes:
        parts: list[bytes] = []
        try:
            for message in _iter_json_objects(body):
                if not isinstance(message, dict):
                    raise TypeError(f"expected object, got {type(message).__name__}")
                if "error" in message:
                    error = message["error"]
                    detail = error.get("message", error) if isinstance(error, dict) else error
                    raise ProviderError(f"Yandex TTS stream error: {str(detail)[:200]}")
                data = message.get("result", {}).get("audioChunk", {}).get("data")
                if data:
                    parts.append(base64.b64decode(data, validate=True))
        except (ValueError, TypeError, AttributeError, binascii.Error) as e:
            raise ProviderError(f"Yandex TTS bad response: {type(e).__name__}: {e}") from e
        if not parts:
            raise ProviderError("Yandex TTS bad response: no audio chunks")
        return b"".join(parts)

    def describe(self) -> dict:
        return {
            "provider": self.name,
            "region": self.region,
            "voice": self.voice,
            "role": self.role,
            "speed": self.speed,
            "pitch": self.pitch,
            "key_loaded": self._api_key is not None,
        }
