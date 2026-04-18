"""
CLEAN_TEXT

Strips formatting from assistant text before TTS synthesis so the voice
does not read code, markdown syntax, or runaway-long responses.

Rules applied in order:
  1. Drop fenced code blocks (``` ... ``` and ~~~ ... ~~~) completely.
  2. Unwrap inline backticks: `foo` -> foo.
  3. Collapse markdown links [label](url) -> label; drop image refs.
  4. Drop raw URLs (http/https).
  5. Strip HTML tags.
  6. Remove markdown structural chars (#, >, list bullets, table pipes, emphasis).
  7. Collapse whitespace.
  8. Truncate to max_chars with a spoken "ответ сокращён" marker.
"""
import re


TRUNCATION_SUFFIX = "… ответ сокращён."


def clean_for_tts(raw: str, max_chars: int = 1500) -> str:
    if not raw:
        return ""

    text = raw

    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"~~~.*?~~~", " ", text, flags=re.DOTALL)

    text = re.sub(r"`([^`]*)`", r"\1", text)

    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    text = re.sub(r"https?://\S+", " ", text)

    text = re.sub(r"<[^>]+>", " ", text)

    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)

    text = re.sub(r"\|", " ", text)

    # INVARIANT: run AFTER list-marker stripping so bullet hyphens are already gone.
    text = re.sub(r"[*_~]", "", text)

    text = re.sub(r"\s+", " ", text).strip()

    if len(text) > max_chars:
        cut = text[:max_chars].rsplit(" ", 1)[0]
        text = cut + " " + TRUNCATION_SUFFIX

    return text
