"""RSSImportExtractor: parse RSS/Atom feeds and return Articles (docs/design.md sec 2.1 + sec 5).

Failure modes (sec 9):
  - Feed URL unreachable (HTTP error) -> "Could not fetch feed: ..."
  - Unparseable response with no entries -> "Could not parse feed: ..."
  - Feed has no entries with usable content -> "Feed contains no usable entries"
"""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import datetime
from typing import Any

import feedparser
import httpx

from audibleweb.db import get_seen_item_ids, mark_items_seen

from .base import Article, ExtractionError, make_article

_TIMEOUT = 30.0
_RSS_URL_PATTERNS = ("/rss", "/feed", "/atom", ".xml", "rss=", "format=rss", "feed=rss")


class RSSImportExtractor:
    name = "rss"
    supported_inputs = ["url:rss", "url:atom"]

    def __init__(self, *, _client: httpx.AsyncClient | None = None) -> None:
        self._client = _client

    def can_handle(self, input: str) -> bool:
        lower = input.lower()
        return lower.startswith(("http://", "https://")) and any(
            p in lower for p in _RSS_URL_PATTERNS
        )

    async def extract(self, input: str) -> Article:
        articles = await self.list_articles(input)
        if not articles:
            raise ExtractionError("Feed contains no usable entries")
        return articles[0]

    async def list_articles(self, feed_url: str) -> list[Article]:
        content = await self._fetch(feed_url)
        feed = feedparser.parse(content)
        if feed.get("bozo") and not feed.entries:
            raise ExtractionError(f"Could not parse feed: {feed.get('bozo_exception')}")
        return [
            a for entry in feed.entries if (a := _entry_to_article(entry)) is not None
        ]

    async def first_subscribe(self, feed_url: str, conn: sqlite3.Connection) -> int:
        """Mark all current feed items seen. Returns count. Creates 0 jobs."""
        content = await self._fetch(feed_url)
        feed = feedparser.parse(content)
        if feed.get("bozo") and not feed.entries:
            raise ExtractionError(f"Could not parse feed: {feed.get('bozo_exception')}")
        item_ids = [iid for e in feed.entries if (iid := _entry_id(e))]
        mark_items_seen(conn, feed_url, item_ids)
        return len(item_ids)

    async def list_new_articles(
        self, feed_url: str, conn: sqlite3.Connection
    ) -> list[Article]:
        """Return articles not previously seen; marks them seen before returning."""
        content = await self._fetch(feed_url)
        feed = feedparser.parse(content)
        if feed.get("bozo") and not feed.entries:
            raise ExtractionError(f"Could not parse feed: {feed.get('bozo_exception')}")
        seen = get_seen_item_ids(conn, feed_url)
        new_ids: list[str] = []
        articles: list[Article] = []
        for entry in feed.entries:
            iid = _entry_id(entry)
            if iid and iid in seen:
                continue
            article = _entry_to_article(entry)
            if article is not None:
                articles.append(article)
            if iid:
                new_ids.append(iid)
        mark_items_seen(conn, feed_url, new_ids)
        return articles

    async def _fetch(self, url: str) -> str:
        try:
            if self._client is not None:
                resp = await self._client.get(url)
            else:
                async with httpx.AsyncClient(
                    timeout=_TIMEOUT, follow_redirects=True
                ) as client:
                    resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
        except httpx.HTTPError as exc:
            raise ExtractionError(f"Could not fetch feed: {exc}") from exc


def _entry_to_article(entry: Any) -> Article | None:
    text = _entry_text(entry)
    title = entry.get("title") or None
    source_url = entry.get("link") or None
    author = entry.get("author") or None
    published = _parse_struct_time(entry.get("published_parsed"))
    try:
        return make_article(
            text, title=title, source_url=source_url, author=author, published=published
        )
    except ExtractionError:
        return None


def _entry_text(entry: Any) -> str:
    content = entry.get("content")
    if content:
        raw = content[0].get("value", "")
    else:
        raw = entry.get("summary", "")
    return _strip_html(raw).strip()


def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


def _entry_id(entry: Any) -> str:
    return entry.get("id") or entry.get("link") or ""


def _parse_struct_time(st: time.struct_time | None) -> datetime | None:
    if st is None:
        return None
    try:
        return datetime(*st[:6])
    except (TypeError, ValueError):
        return None
