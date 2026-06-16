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

    async def publish_and_update_feed(
        self,
        episode: Episode,
        audio_path: Path,
        all_episodes: list[Episode],
    ) -> tuple[str, str]:
        """Stage MP3 + feed.xml atomically, returning (public_url, feed_url).

        Default: calls publish() then update_feed() sequentially (fine for
        LocalPublisher where file writes are already atomic enough).
        GitHubPagesPublisher overrides this to do a single git commit+push so a
        crash between operations can't leave the gh-pages branch in a broken state.
        """
        public_url = await self.publish(episode, audio_path)
        feed_url = await self.update_feed(all_episodes)
        return public_url, feed_url


def episode_slug(title: str, published: datetime) -> str:
    """Sanitized title + date, e.g. '2026-06-13-article-title' (docs/design.md sec 6)."""
    slug_title = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    date_part = published.strftime("%Y-%m-%d")
    return f"{date_part}-{slug_title}" if slug_title else date_part
