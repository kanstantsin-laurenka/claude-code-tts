"""
OPENAI PROVIDER

Wraps OpenAI's audio/speech endpoint via the official async SDK. Reads the API
key from the macOS Keychain at construction; if missing, synthesize() raises
ProviderError so the server returns a clean 502 instead of crashing.

Business rules:
  - No pre-flight validation of the key — first /speak surfaces auth errors
    naturally via 502, matching how edge-tts failures behave today.
  - speed is clamped client-side to OpenAI's supported 0.25–4.0 range just in
    case an invalid env var slips past bin/tts validation.
  - tts-1 / tts-1-hd / gpt-4o-mini-tts all share the same call shape.
  - `instructions` (free-form tone/style text) is only supported by
    gpt-4o-mini-tts. For tts-1 / tts-1-hd we silently drop it and log once at
    construction — the user keeps the setting in the plist and it becomes
    active again as soon as they switch model, without a 400 in the middle.

Dependencies: openai (pip).
"""

import sys

from openai import APIError, AsyncOpenAI, AuthenticationError, BadRequestError, RateLimitError

from . import ProviderError, TTSProvider

SPEED_MIN = 0.25
SPEED_MAX = 4.0

INSTRUCTIONS_CAPABLE_MODELS = frozenset({"gpt-4o-mini-tts"})


class OpenAIProvider(TTSProvider):
    name = "openai"

    def __init__(
        self,
        model: str,
        voice: str,
        speed: float,
        api_key: str | None,
        instructions: str | None = None,
    ) -> None:
        self.model = model
        self.voice = voice
        clamped = max(SPEED_MIN, min(SPEED_MAX, speed))
        if clamped != speed:
            # Surface silent clamping — a manual plist edit could otherwise put
            # speed outside [0.25, 4.0] and the user would never know.
            print(
                f"[tts] OPENAI_TTS_SPEED={speed} out of [{SPEED_MIN}, {SPEED_MAX}], "
                f"clamped to {clamped}",
                file=sys.stderr,
                flush=True,
            )
        self.speed = clamped
        # Normalize: empty/whitespace-only strings behave as "not set" so we
        # never send an empty `instructions` field to the API.
        cleaned_instructions = (instructions or "").strip() or None
        self.instructions = cleaned_instructions
        if cleaned_instructions and model not in INSTRUCTIONS_CAPABLE_MODELS:
            # One-shot warn at construction. We deliberately keep the field on
            # `self` so `tts config`/`/health` still reflect the user's intent,
            # and the moment they switch to gpt-4o-mini-tts it becomes active.
            print(
                f"[tts] OPENAI_TTS_INSTRUCTIONS ignored: model={model} does not "
                "support instructions (only gpt-4o-mini-tts does)",
                file=sys.stderr,
                flush=True,
            )
        self.api_key = api_key
        self._client: AsyncOpenAI | None = None
        if api_key:
            self._client = AsyncOpenAI(api_key=api_key)

    async def synthesize(self, text: str, out_path: str) -> None:
        if self._client is None:
            raise ProviderError(
                "OpenAI API key missing — set it with: tts openai-key",
            )
        # Only forward instructions when the model accepts it. Anything else
        # triggers a 400 from the API — the very failure mode this feature is
        # designed to avoid.
        extra: dict = {}
        if self.instructions and self.model in INSTRUCTIONS_CAPABLE_MODELS:
            extra["instructions"] = self.instructions
        try:
            async with self._client.audio.speech.with_streaming_response.create(
                model=self.model,
                voice=self.voice,
                input=text,
                speed=self.speed,
                response_format="mp3",
                **extra,
            ) as response:
                await response.stream_to_file(out_path)
        except AuthenticationError as e:
            raise ProviderError(f"OpenAI auth failed: {e}") from e
        except RateLimitError as e:
            raise ProviderError(f"OpenAI rate limit / quota: {e}") from e
        except BadRequestError as e:
            raise ProviderError(f"OpenAI bad request: {e}") from e
        except APIError as e:
            raise ProviderError(f"OpenAI API error: {e}") from e
        except Exception as e:
            raise ProviderError(f"OpenAI call failed: {e}") from e

    def describe(self) -> dict:
        return {
            "provider": self.name,
            "model": self.model,
            "voice": self.voice,
            "speed": self.speed,
            "key_loaded": self._client is not None,
            # active=True only when the model actually accepts it — mirrors
            # synthesize()'s gating so /health reflects runtime behavior, not
            # just what the user configured.
            "instructions_active": bool(
                self.instructions and self.model in INSTRUCTIONS_CAPABLE_MODELS
            ),
        }
