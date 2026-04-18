"""
CLEAN_TEXT

Strips formatting from assistant text before TTS synthesis so the voice
does not read code, markdown syntax, or runaway-long responses. Also
extracts the TL;DR block when the server is configured to speak only
the summary.

Rules applied by clean_for_tts, in order:
  1. Drop fenced code blocks (``` ... ``` and ~~~ ... ~~~) completely.
  2. Unwrap inline backticks: `foo` -> foo.
  3. Collapse markdown links [label](url) -> label; drop image refs.
  4. Drop raw URLs (http/https).
  5. Strip HTML tags.
  6. Remove markdown structural chars (#, >, list bullets, table pipes, emphasis).
  7. Collapse whitespace.
  8. Truncate to max_chars with a spoken "ответ сокращён" marker.

extract_tldr finds the LAST "TL;DR" marker (any of `## TL;DR`, `**TL;DR**`,
`TL;DR:`, `TL;DR`) and returns the text after it. If no marker is present
it returns None — the server skips playback entirely when TTS_TLDR_ONLY=1.
"""

import re

TRUNCATION_SUFFIX = "… ответ сокращён."

# Matches a TL;DR marker at the start of a line. Accepts optional markdown
# heading (#–######), optional surrounding ** bold, and optional trailing colon.
# Everything AFTER the match is the summary body.
TLDR_MARKER_RE = re.compile(
    r"^\s*(?:#{1,6}\s+)?\**\s*TL;DR\**\s*:?\s*",
    re.IGNORECASE | re.MULTILINE,
)


def extract_tldr(raw: str | None) -> str | None:
    """Return the body after the last TL;DR marker, or None if absent."""
    if not raw:
        return None
    matches = list(TLDR_MARKER_RE.finditer(raw))
    if not matches:
        return None
    body = raw[matches[-1].end() :].strip()
    return body or None


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
