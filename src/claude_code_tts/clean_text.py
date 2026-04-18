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
`TL;DR:`, `TL;DR`) and returns the text after it. Fenced code blocks are
stripped before the search so a documentation snippet containing an example
TL;DR is not mistaken for a real marker. If no marker is present it returns
None — the server skips playback entirely when TTS_TLDR_ONLY=1.
"""

import re

TRUNCATION_SUFFIX = "… ответ сокращён."

_CODE_FENCE_BACKTICKS_RE = re.compile(r"```.*?```", re.DOTALL)
_CODE_FENCE_TILDES_RE = re.compile(r"~~~.*?~~~", re.DOTALL)

# Matches a TL;DR marker at the start of a line. Accepts:
#   - `TL;DR`, `TL;DR:` (bare)
#   - `**TL;DR**` (symmetric bold)
#   - `## TL;DR`, `**TL;DR**` after an ATX heading
# Rejects asymmetric markdown like `*TL;DR*`, `*TL;DR`, `TL;DR**`, `***TL;DR***` —
# those are typos or other constructs and shouldn't trigger TL;DR-only mode.
# Everything AFTER the match is the summary body.
TLDR_MARKER_RE = re.compile(
    r"^\s*(?:#{1,6}\s+)?(?:\*\*\s*TL;DR\s*\*\*|TL;DR(?!\*))\s*:?\s*",
    re.IGNORECASE | re.MULTILINE,
)


def _strip_code_fences(text: str) -> str:
    """Remove fenced code blocks (``` ... ``` and ~~~ ... ~~~)."""
    text = _CODE_FENCE_BACKTICKS_RE.sub(" ", text)
    text = _CODE_FENCE_TILDES_RE.sub(" ", text)
    return text


def extract_tldr(raw: str | None) -> str | None:
    """Return the body after the last TL;DR marker, or None if absent.

    Fenced code blocks are stripped before the search so that example TL;DR
    markers inside documentation snippets are ignored.
    """
    if not raw:
        return None
    searchable = _strip_code_fences(raw)
    matches = list(TLDR_MARKER_RE.finditer(searchable))
    if not matches:
        return None
    body = searchable[matches[-1].end() :].strip()
    return body or None


def clean_for_tts(raw: str, max_chars: int = 1500) -> str:
    if not raw:
        return ""

    text = _strip_code_fences(raw)

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
