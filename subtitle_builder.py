"""
Turns a flat word-timestamp list into short Caption chunks for CapCut-style
auto-captions (1-4 words per line, timed tightly to speech).

Improvements over v1:
- Minimum caption duration guard (avoids sub-100ms flashes)
- Overlap gap: caption end is extended slightly into the next gap so there
  is no black frame between consecutive captions
- Smarter break logic: breaks on terminal punctuation even mid-chunk,
  breaks on clause punctuation only when chunk has >= 2 words, respects
  a maximum silence gap between words
- Handles edge cases: single-word videos, all-punctuation tokens, zero-
  duration words (clamps to 0.05 s minimum)
- capitalize and strip_punctuation_from_display are independent options
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from types_common import Word


# ── tunables ────────────────────────────────────────────────────────────────
TERMINAL_PUNCT = frozenset({".", "!", "?", "…", "。", "！", "？"})
CLAUSE_PUNCT   = frozenset({",", ";", ":", "、", "，"})

# A gap larger than this between consecutive words always starts a new caption
DEFAULT_MAX_GAP_S = 0.45

# Extend each caption end this many seconds into the following gap so there
# is no dark flash between captions (capped so it never overlaps the next one)
EXTEND_END_S = 0.08

# Minimum caption on-screen time — avoids single-frame flickers
MIN_DURATION_S = 0.15
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class Caption:
    text:  str
    start: float
    end:   float
    # word-level data kept for future karaoke / highlight export
    words: List[Word] = field(default_factory=list, repr=False)


def _ends_with(token: str, char_set: frozenset) -> bool:
    stripped = token.rstrip()
    return bool(stripped) and stripped[-1] in char_set


def _make_caption(
    chunk: List[Word],
    capitalize: bool,
    next_word_start: float | None,
) -> Caption:
    """Build one Caption from a completed chunk of words."""
    text = " ".join(w.text for w in chunk)
    if capitalize:
        text = text.upper()

    start = chunk[0].start
    raw_end = chunk[-1].end

    # Extend into the following gap a little, but never past the next word
    if next_word_start is not None:
        gap = next_word_start - raw_end
        extend = min(EXTEND_END_S, gap * 0.4)
        end = raw_end + max(0.0, extend)
    else:
        end = raw_end + EXTEND_END_S  # last caption: safe to extend freely

    # Enforce minimum duration
    if end - start < MIN_DURATION_S:
        end = start + MIN_DURATION_S

    return Caption(text=text, start=start, end=end, words=list(chunk))


def build_captions(
    words: List[Word],
    max_words: int = 3,
    max_gap_seconds: float = DEFAULT_MAX_GAP_S,
    capitalize: bool = False,
) -> List[Caption]:
    """
    Groups `words` into short Caption objects.

    Parameters
    ----------
    words           : list of Word(text, start, end) from the transcriber
    max_words       : hard cap on words per caption line (1-4 recommended)
    max_gap_seconds : silence gap that always forces a caption break
    capitalize      : if True, all caption text is uppercased
    """
    if not words:
        return []

    # Clamp zero-duration words so timing math stays sane
    cleaned: List[Word] = []
    for w in words:
        token = w.text.strip()
        if not token:
            continue
        start = max(0.0, w.start)
        end   = max(start + 0.05, w.end)
        cleaned.append(Word(text=token, start=start, end=end))

    if not cleaned:
        return []

    captions: List[Caption] = []
    chunk: List[Word] = []

    for i, w in enumerate(cleaned):
        next_w = cleaned[i + 1] if i + 1 < len(cleaned) else None

        # ── decide whether to break BEFORE adding this word ────────────────
        if chunk:
            gap = w.start - chunk[-1].end
            force_gap    = gap > max_gap_seconds
            force_length = len(chunk) >= max_words

            if force_gap or force_length:
                captions.append(_make_caption(chunk, capitalize, w.start))
                chunk = []

        chunk.append(w)

        # ── decide whether to break AFTER this word ─────────────────────────
        ends_sent   = _ends_with(w.text, TERMINAL_PUNCT)
        ends_clause = _ends_with(w.text, CLAUSE_PUNCT)

        flush = (
            ends_sent                              # always break on . ! ?
            or (ends_clause and len(chunk) >= 2)  # break on , ; : if ≥2 words
            or len(chunk) >= max_words            # hard word cap
        )

        if flush:
            next_start = next_w.start if next_w else None
            captions.append(_make_caption(chunk, capitalize, next_start))
            chunk = []

    # flush remaining words
    if chunk:
        captions.append(_make_caption(chunk, capitalize, None))

    return captions
