"""Publisher plugin protocol + shared Episode representation (docs/design.md sec 2.3, 6)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class Episode:
    title: str
    published: datetime  # tz-aware (UTC)
    duration_sec: float
    source_url: str | None
    public_url: str = ""  # set from publish()'s return value once known
    file_size_bytes: int = 0  # set from the stitched MP3's size once known


@runtime_checkable
class Publisher(Protocol):
    name: str

    async def publish(
        self, episode: Episode, audio_path: Path
    ) -> str: ...  # returns public URL

    async def update_feed(self, episodes: list[Episode]) -> str: ...  # returns feed URL


def episode_slug(title: str, published: datetime) -> str:
    """Sanitized title + date, e.g. '2026-06-13-article-title' (docs/design.md sec 6)."""
    slug_title = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    date_part = published.strftime("%Y-%m-%d")
    return f"{date_part}-{slug_title}" if slug_title else date_part
