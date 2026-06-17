"""LocalPublisher: serve data/audio/ + feed.xml locally for dev (docs/design.md sec 2.3)."""

from __future__ import annotations

import shutil
from pathlib import Path

from audibleweb.core.feed import FeedConfig, generate_feed, validate_feed
from audibleweb.publishers.base import Episode, episode_slug


class LocalPublisher:
    name = "local"

    def __init__(self, data_dir: Path | str, base_url: str, feed_config: FeedConfig):
        self.data_dir = Path(data_dir)
        self.audio_dir = self.data_dir / "audio"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url.rstrip("/")
        self.feed_config = feed_config

    async def publish(self, episode: Episode, audio_path: Path) -> str:
        slug = episode_slug(episode.title, episode.published)
        dest = self.audio_dir / f"{slug}.mp3"
        shutil.copy2(audio_path, dest)
        episode.public_url = f"{self.base_url}/audio/{slug}.mp3"
        return episode.public_url

    async def update_feed(self, episodes: list[Episode]) -> str:
        xml = generate_feed(episodes, self.feed_config)
        validate_feed(xml)
        (self.data_dir / "feed.xml").write_text(xml, encoding="utf-8")
        return f"{self.base_url}/feed.xml"

    async def publish_and_update_feed(
        self,
        episode: Episode,
        audio_path: Path,
        all_episodes: list[Episode],
    ) -> tuple[str, str]:
        public_url = await self.publish(episode, audio_path)
        feed_url = await self.update_feed(all_episodes)
        return public_url, feed_url
