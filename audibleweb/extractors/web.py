"""WebExtractor: trafilatura primary + Jina Reader API fallback (docs/design.md sec 2.1 + 2.4).

Failure modes (sec 9):
  - URL unreachable (HTTP error / connection error) -> "Could not fetch URL"
  - Trafilatura yields <100 chars, jina_fallback disabled -> "No extractable content"
  - Trafilatura yields <100 chars, Jina also fails -> "Extraction failed (both methods)"
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import trafilatura

from .base import MIN_CONTENT_CHARS, Article, ExtractionError, make_article

_JINA_BASE = "https://r.jina.ai/"
_USER_AGENT = "Mozilla/5.0 (compatible; AudibleWeb/1.0)"
_TIMEOUT = 30.0


class WebExtractor:
    name = "web"
    supported_inputs = ["url"]

    def __init__(
        self,
        *,
        jina_fallback: bool = True,
        jina_api_key: str = "",
        _client: httpx.AsyncClient | None = None,
    ) -> None:
        self._jina_fallback = jina_fallback
        self._jina_api_key = jina_api_key
        self._client = _client

    def can_handle(self, input: str) -> bool:
        return input.startswith(("http://", "https://"))

    async def extract(self, input: str) -> Article:
        url = input
        html = await self._fetch_html(url)
        text, title, author, published = _run_trafilatura(html, url)

        if text and len(text) >= MIN_CONTENT_CHARS:
            return make_article(
                text, title=title, source_url=url, author=author, published=published
            )

        if self._jina_fallback:
            return await self._extract_jina(url)

        raise ExtractionError("No extractable content")

    async def _fetch_html(self, url: str) -> str:
        try:
            resp = await self._get(url, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            return resp.text
        except httpx.HTTPError as exc:
            raise ExtractionError(f"Could not fetch URL: {exc}") from exc

    async def _extract_jina(self, url: str) -> Article:
        jina_url = _JINA_BASE + url
        headers: dict[str, str] = {"Accept": "text/plain"}
        if self._jina_api_key:
            headers["Authorization"] = f"Bearer {self._jina_api_key}"
        try:
            resp = await self._get(jina_url, headers=headers)
            resp.raise_for_status()
            text = resp.text.strip()
        except httpx.HTTPError as exc:
            raise ExtractionError("Extraction failed (both methods)") from exc

        if len(text) < MIN_CONTENT_CHARS:
            raise ExtractionError("Extraction failed (both methods)")

        return make_article(text, source_url=url)

    async def _get(self, url: str, **kwargs: Any) -> httpx.Response:
        if self._client is not None:
            return await self._client.get(url, **kwargs)
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            return await client.get(url, **kwargs)


def _run_trafilatura(
    html: str, url: str
) -> tuple[str | None, str | None, str | None, datetime | None]:
    result = trafilatura.bare_extraction(html, url=url, include_comments=False)
    if result is None:
        return None, None, None, None
    text = getattr(result, "text", None)
    title = getattr(result, "title", None)
    author = getattr(result, "author", None)
    date_str = getattr(result, "date", None)
    return text, title, author, _parse_date(date_str) if date_str else None


def _parse_date(date_str: str) -> datetime | None:
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None
