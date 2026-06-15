"""Extractor plugin protocol + shared Article representation (docs/design.md sec 2.1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

MIN_CONTENT_CHARS = 100


class ExtractionError(Exception):
    """Raised when an extractor cannot produce usable Article content (docs/design.md sec 9)."""


@dataclass
class Article:
    title: str
    text: str  # plaintext, ready for the cleaning stage (docs/design.md sec 3)
    source_url: str | None
    author: str | None
    published: datetime | None
    word_count: int


@runtime_checkable
class Extractor(Protocol):
    name: str
    supported_inputs: list[str]  # e.g. ["url"], ["file:pdf", "file:txt", "file:md"]

    def can_handle(self, input: str) -> bool: ...

    async def extract(self, input: str) -> Article: ...


def derive_title(text: str, max_len: int = 100) -> str:
    """Fallback title: first non-empty line, markdown heading markers stripped, truncated."""
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line if len(line) <= max_len else line[: max_len - 1].rstrip() + "…"
    return "Untitled"


def make_article(
    text: str,
    *,
    title: str | None = None,
    source_url: str | None = None,
    author: str | None = None,
    published: datetime | None = None,
) -> Article:
    """Build an Article, enforcing the "No extractable content" failure mode (sec 9)."""
    text = text.strip()
    if len(text) < MIN_CONTENT_CHARS:
        raise ExtractionError("No extractable content")
    return Article(
        title=title or derive_title(text),
        text=text,
        source_url=source_url,
        author=author,
        published=published,
        word_count=len(text.split()),
    )
