"""
OPENAI VOICE AUDITION

Synthesizes the same short sample with every voice the currently-configured
OpenAI TTS model supports, plays them back with an interactive rating prompt,
and optionally applies the top pick as the active voice.

Business rules:
  - Reuses current plist settings (model, speed, instructions) so the audition
    matches how the daemon actually speaks — not a synthetic "reference" config.
  - `instructions` is only honored by gpt-4o-mini-tts. We mirror the gating in
    src/claude_code_tts/providers/openai.py and drop it for other models.
  - All synth requests run concurrently — sequential would add 30–60s of API
    latency to what should feel like a snappy CLI.
  - Output mp3s stay in /tmp/tts-audition/ so the user can re-listen afterwards.
  - Voice↔model matrix is duplicated from bin/tts (_openai_voices_for_model).
    Both sources must stay in sync; the CLI already validates pairings so any
    drift will be caught quickly.

Dependencies: openai (pip), afplay, say, PlistBuddy (all macOS-native except openai).

Usage:
    venv/bin/python scripts/audition_openai_voices.py
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Run without installing the package: src/ layout means we need it on sys.path
# for `claude_code_tts.keychain` to import.
sys.path.insert(0, str(REPO_ROOT / "src"))

from claude_code_tts.keychain import get_openai_key  # noqa: E402

try:
    from openai import AsyncOpenAI
except ImportError:
    print(
        "error: openai package not installed — run: venv/bin/pip install -r requirements.txt",
        file=sys.stderr,
    )
    sys.exit(1)


VOICES_BY_MODEL: dict[str, list[str]] = {
    "tts-1": ["alloy", "ash", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer"],
    "tts-1-hd": ["alloy", "ash", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer"],
    "gpt-4o-mini-tts": [
        "alloy",
        "ash",
        "ballad",
        "coral",
        "echo",
        "fable",
        "marin",
        "nova",
        "onyx",
        "sage",
        "shimmer",
        "verse",
        "cedar",
    ],
}

INSTRUCTIONS_CAPABLE_MODELS = frozenset({"gpt-4o-mini-tts"})

SAMPLE_TEXT = "Задача выполнена. Все тесты прошли, код готов к ревью. Можно коммитить."

OUTPUT_DIR = Path("/tmp/tts-audition")

PLIST_PATH = Path.home() / "Library/LaunchAgents/com.claude-code.tts.plist"


@dataclass
class Clip:
    voice: str
    path: Path
    size_bytes: int
    duration_s: float


def read_plist_value(key: str, default: str) -> str:
    if not PLIST_PATH.exists():
        return default
    result = subprocess.run(
        [
            "/usr/libexec/PlistBuddy",
            "-c",
            f"Print :EnvironmentVariables:{key}",
            str(PLIST_PATH),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return default
    return result.stdout.strip() or default


async def _synthesize_one(
    client: AsyncOpenAI,
    model: str,
    voice: str,
    speed: float,
    instructions: str | None,
    out_path: Path,
) -> None:
    extra: dict = {}
    if instructions and model in INSTRUCTIONS_CAPABLE_MODELS:
        extra["instructions"] = instructions
    async with client.audio.speech.with_streaming_response.create(
        model=model,
        voice=voice,
        input=SAMPLE_TEXT,
        speed=speed,
        response_format="mp3",
        **extra,
    ) as response:
        await response.stream_to_file(str(out_path))


async def synthesize_all(
    model: str,
    voices: list[str],
    speed: float,
    instructions: str | None,
    api_key: str,
) -> list[Clip]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    client = AsyncOpenAI(api_key=api_key)
    out_paths = [OUTPUT_DIR / f"{voice}.mp3" for voice in voices]
    try:
        await asyncio.gather(
            *[
                _synthesize_one(client, model, v, speed, instructions, p)
                for v, p in zip(voices, out_paths, strict=True)
            ]
        )
    finally:
        await client.close()

    clips: list[Clip] = []
    for voice, path in zip(voices, out_paths, strict=True):
        size = path.stat().st_size if path.exists() else 0
        duration = _afinfo_duration(path) if path.exists() else 0.0
        clips.append(Clip(voice=voice, path=path, size_bytes=size, duration_s=duration))
    return clips


def _afinfo_duration(path: Path) -> float:
    result = subprocess.run(["afinfo", str(path)], capture_output=True, text=True)
    if result.returncode != 0:
        return 0.0
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if line.startswith("estimated duration:"):
            try:
                return float(line.split(":", 1)[1].strip().split()[0])
            except (ValueError, IndexError):
                return 0.0
    return 0.0


def announce(voice: str) -> None:
    # Spoken label helps when the user is focused on listening rather than the
    # terminal. English `say` voice keeps it distinct from the audition clip.
    subprocess.run(["say", f"Voice {voice}"], check=False)


def play(path: Path) -> None:
    subprocess.run(["afplay", str(path)], check=False)


def _prompt_rating(voice: str) -> int | None:
    """Returns 1–5 rating, None if skipped, -1 if replay requested.

    KeyboardInterrupt bubbles up for 'q' so the caller can finish gracefully.
    """
    while True:
        choice = input(f"  [{voice}] rate 1-5, r=replay, s=skip, q=quit: ").strip().lower()
        if choice == "q":
            raise KeyboardInterrupt
        if choice == "s":
            return None
        if choice == "r":
            return -1
        if choice in {"1", "2", "3", "4", "5"}:
            return int(choice)
        print("    invalid input — try 1-5, r, s, or q")


def audition(clips: list[Clip]) -> dict[str, int]:
    ratings: dict[str, int] = {}
    print()
    print(f"Playing {len(clips)} clips. Rate each, or skip/quit.")
    print()

    for i, clip in enumerate(clips, 1):
        print(
            f"[{i}/{len(clips)}] voice={clip.voice}  "
            f"size={clip.size_bytes / 1024:.1f}KB  duration={clip.duration_s:.1f}s"
        )
        while True:
            announce(clip.voice)
            play(clip.path)
            r = _prompt_rating(clip.voice)
            if r == -1:
                continue
            if r is None:
                break
            ratings[clip.voice] = r
            break

    return ratings


def print_report(clips: list[Clip], ratings: dict[str, int]) -> str | None:
    print()
    print("=" * 60)
    print("Audition report")
    print("=" * 60)

    def sort_key(c: Clip) -> tuple[int, int]:
        # Rated voices first (group 0), highest rating at the top.
        return (0 if c.voice in ratings else 1, -ratings.get(c.voice, 0))

    ordered = sorted(clips, key=sort_key)

    header = f"  {'voice':<10}  {'rating':>6}  {'size':>8}  {'dur':>6}"
    print(header)
    print(f"  {'-' * 10}  {'-' * 6}  {'-' * 8}  {'-' * 6}")
    for c in ordered:
        rating = str(ratings.get(c.voice, "-"))
        print(f"  {c.voice:<10}  {rating:>6}  {c.size_bytes / 1024:>6.1f}KB  {c.duration_s:>5.1f}s")

    if not ratings:
        print("\nNo ratings given.")
        print(f"Clips remain in {OUTPUT_DIR} — re-listen with: afplay {OUTPUT_DIR}/<voice>.mp3")
        return None

    top_voice = max(ratings, key=lambda v: ratings[v])
    print(f"\nTop pick: {top_voice} (rating {ratings[top_voice]})")
    print(f"Files stay in {OUTPUT_DIR} — re-listen with: afplay {OUTPUT_DIR}/<voice>.mp3")
    return top_voice


def maybe_apply(top_voice: str) -> None:
    try:
        choice = input(f"\nApply '{top_voice}' as active OpenAI voice? [y/N]: ").strip().lower()
    except EOFError:
        return
    if choice != "y":
        print("Skipped — voice unchanged.")
        return
    tts_bin = REPO_ROOT / "bin" / "tts"
    subprocess.run([str(tts_bin), "openai-voice", top_voice], check=False)


def main() -> int:
    api_key = get_openai_key()
    if not api_key:
        print("error: OpenAI key not in Keychain — run: tts openai-key", file=sys.stderr)
        return 1

    model = read_plist_value("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
    current_voice = read_plist_value("OPENAI_TTS_VOICE", "nova")
    speed_raw = read_plist_value("OPENAI_TTS_SPEED", "1.0")
    instructions = read_plist_value("OPENAI_TTS_INSTRUCTIONS", "") or None

    try:
        speed = float(speed_raw)
    except ValueError:
        speed = 1.0

    voices = VOICES_BY_MODEL.get(model)
    if not voices:
        known = sorted(VOICES_BY_MODEL)
        print(
            f"error: unknown OPENAI_TTS_MODEL '{model}' — expected one of {known}",
            file=sys.stderr,
        )
        return 1

    instr_display = instructions or "(none)"
    if instructions and len(instructions) > 60:
        instr_display = instructions[:57] + "..."

    print(f"Model:         {model}")
    print(f"Current voice: {current_voice}")
    print(f"Speed:         {speed}")
    print(f"Instructions:  {instr_display}")
    if instructions and model not in INSTRUCTIONS_CAPABLE_MODELS:
        print(f"               ! ignored by {model} (only gpt-4o-mini-tts honors it)")
    print(f"Sample text:   «{SAMPLE_TEXT}»")
    print(f"Output dir:    {OUTPUT_DIR}")
    print()
    print(f"Synthesizing {len(voices)} voices in parallel...")

    try:
        clips = asyncio.run(synthesize_all(model, voices, speed, instructions, api_key))
    except Exception as e:
        print(f"error: synthesis failed: {e}", file=sys.stderr)
        return 1

    try:
        ratings = audition(clips)
    except KeyboardInterrupt:
        print("\nAborted by user.")
        return 0

    top = print_report(clips, ratings)
    if top:
        try:
            maybe_apply(top)
        except KeyboardInterrupt:
            print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
