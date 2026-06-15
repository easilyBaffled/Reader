"""Split cleaned, normalized text into chunks for TTS synthesis.

Adapted from abogen's core/chunking.py::chunk_text, reduced for AudibleWeb's
single-voice, no-chapters pipeline (docs/design.md sec 3 + D10): no
chapter_index/speaker_id/voice_profile/voice_formula and no
build_chunks_for_chapters.

AudibleWeb's pipeline order is extract -> clean -> normalize -> pronunciation
-> chunk (docs/design.md sec 3), so normalization is already done by the time
text reaches chunk_text. Unlike abogen, chunk_text here does not call
kokoro_text_normalization itself and has no normalized_text/display_text/
original_text tracking — see reader-8f2.2's notes (bd show reader-8f2.2) and
.pocock/progress.md for the kokoro_text_normalization scope decision.
"""

from __future__ import annotations

import re
from typing import Literal

ChunkLevel = Literal["paragraph", "sentence"]

_PARAGRAPH_SPLIT_RE = re.compile(r"(?:\r?\n){2,}")
_SENTENCE_SPLIT_RE = re.compile(r"(?<!\b[A-Z])[.!?][\s\n]+")
_WHITESPACE_RE = re.compile(r"\s+")
_ABBREVIATION_END_RE = re.compile(
    r"\b(?:Mr|Mrs|Ms|Dr|Prof|Rev|Sr|Jr|St|Gen|Lt|Col|Sgt|Capt|Adm|Cmdr|vs|etc)\.$",
    re.IGNORECASE,
)


def chunk_text(text: str, level: ChunkLevel) -> list[str]:
    """Split text into paragraph or sentence chunks. No mid-sentence splits."""
    paragraphs = _split_paragraphs(text)

    if level == "paragraph":
        return [_normalize_whitespace(p) for p in paragraphs]

    chunks: list[str] = []
    for paragraph in paragraphs:
        chunks.extend(_normalize_whitespace(s) for s in _split_sentences(paragraph))
    return chunks


def _split_paragraphs(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    return [p for p in (p.strip() for p in _PARAGRAPH_SPLIT_RE.split(stripped)) if p]


def _split_sentences(paragraph: str) -> list[str]:
    """Split into sentences, merging ones ending in an abbreviation (e.g. "Dr.")."""
    candidates = _sentence_candidates(paragraph)
    if not candidates:
        return [paragraph]

    merged: list[str] = []
    buffer: list[str] = []
    for candidate in candidates:
        buffer.append(candidate)
        if _ABBREVIATION_END_RE.search(candidate):
            continue
        merged.append(" ".join(buffer))
        buffer = []
    if buffer:
        merged.append(" ".join(buffer))
    return merged


def _sentence_candidates(paragraph: str) -> list[str]:
    candidates: list[str] = []
    start = 0
    for match in _SENTENCE_SPLIT_RE.finditer(paragraph):
        end = match.end()
        candidate = paragraph[start:end].strip()
        if candidate:
            candidates.append(candidate)
        start = end
    tail = paragraph[start:].strip()
    if tail:
        candidates.append(tail)
    return candidates


def _normalize_whitespace(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value).strip()
