"""
EDGE-TTS PROVIDER

Wraps Microsoft edge-tts. Free, offline-ish (hits Microsoft's public endpoint),
supports rate/pitch. This is the default provider to preserve behavior for
everyone who already installed the tool before the provider split landed.
"""

import edge_tts

from . import ProviderError, TTSProvider


class EdgeTTSProvider(TTSProvider):
    name = "edge-tts"

    def __init__(self, voice: str, rate: str, pitch: str) -> None:
        self.voice = voice
        self.rate = rate
        self.pitch = pitch

    async def synthesize(self, text: str, out_path: str) -> None:
        try:
            communicate = edge_tts.Communicate(text, self.voice, rate=self.rate, pitch=self.pitch)
            await communicate.save(out_path)
        except Exception as e:
            raise ProviderError(f"edge-tts error: {e}") from e

    def describe(self) -> dict:
        return {
            "provider": self.name,
            "voice": self.voice,
            "rate": self.rate,
            "pitch": self.pitch,
        }
