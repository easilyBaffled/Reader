"""Tests for RSSImportExtractor (audibleweb/extractors/rss.py)."""

import asyncio

import httpx
import pytest

from audibleweb.extractors.base import ExtractionError
from audibleweb.extractors.rss import RSSImportExtractor

LONG_BODY = (
    "This is a sufficiently detailed article body for RSS extraction tests. " * 3
)


RSS_FEED = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Blog</title>
    <link>http://example.com</link>
    <item>
      <title>First Post</title>
      <link>http://example.com/first</link>
      <author>Alice</author>
      <pubDate>Mon, 01 Jan 2024 12:00:00 +0000</pubDate>
      <description>{body}</description>
    </item>
    <item>
      <title>Second Post</title>
      <link>http://example.com/second</link>
      <description>{body}</description>
    </item>
  </channel>
</rss>
""".format(body=LONG_BODY)

ATOM_FEED = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Blog</title>
  <entry>
    <title>Atom Entry</title>
    <link href="http://example.com/atom/1"/>
    <author><name>Bob</name></author>
    <published>2024-06-01T10:00:00Z</published>
    <summary>{body}</summary>
  </entry>
</feed>
""".format(body=LONG_BODY)

RSS_WITH_HTML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>HTML Blog</title>
    <item>
      <title>HTML Post</title>
      <link>http://example.com/html</link>
      <description>&lt;p&gt;{body}&lt;/p&gt;&lt;a href="x"&gt;link&lt;/a&gt;</description>
    </item>
  </channel>
</rss>
""".format(body=LONG_BODY)

RSS_WITH_CONTENT = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Content Blog</title>
    <item>
      <title>Full Content Post</title>
      <link>http://example.com/content</link>
      <description>Short summary.</description>
      <content:encoded>{body}</content:encoded>
    </item>
  </channel>
</rss>
""".format(body=LONG_BODY)

RSS_SHORT_ENTRIES = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Short Blog</title>
    <item>
      <title>Too Short</title>
      <link>http://example.com/short</link>
      <description>Brief.</description>
    </item>
  </channel>
</rss>
"""

RSS_EMPTY = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Empty Blog</title>
  </channel>
</rss>
"""

FEED_URL = "http://example.com/rss"


def run(coro):
    return asyncio.run(coro)


def _client_returning(body: str, status: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status, text=body, headers={"content-type": "application/rss+xml"}
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _client_raising(exc: Exception) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- can_handle ---------------------------------------------------------------


def test_can_handle_rss_url():
    assert RSSImportExtractor().can_handle("http://example.com/rss") is True


def test_can_handle_feed_url():
    assert RSSImportExtractor().can_handle("https://blog.example.com/feed") is True


def test_can_handle_atom_url():
    assert RSSImportExtractor().can_handle("https://example.com/atom.xml") is True


def test_cannot_handle_generic_url():
    assert RSSImportExtractor().can_handle("https://example.com/article") is False


def test_cannot_handle_plain_text():
    assert RSSImportExtractor().can_handle("just some text") is False


# --- list_articles: RSS -------------------------------------------------------


def test_list_articles_rss_feed():
    extractor = RSSImportExtractor(_client=_client_returning(RSS_FEED))
    articles = run(extractor.list_articles(FEED_URL))
    assert len(articles) == 2
    assert articles[0].title == "First Post"
    assert articles[0].source_url == "http://example.com/first"
    assert articles[0].author == "Alice"
    assert articles[0].published is not None
    assert articles[0].published.year == 2024
    assert LONG_BODY.strip() in articles[0].text


def test_list_articles_atom_feed():
    extractor = RSSImportExtractor(_client=_client_returning(ATOM_FEED))
    articles = run(extractor.list_articles(FEED_URL))
    assert len(articles) == 1
    assert articles[0].title == "Atom Entry"
    assert articles[0].author == "Bob"


def test_list_articles_strips_html_from_summary():
    extractor = RSSImportExtractor(_client=_client_returning(RSS_WITH_HTML))
    articles = run(extractor.list_articles(FEED_URL))
    assert len(articles) == 1
    assert "<p>" not in articles[0].text
    assert "<a" not in articles[0].text
    assert LONG_BODY.strip() in articles[0].text


def test_list_articles_prefers_content_over_summary():
    extractor = RSSImportExtractor(_client=_client_returning(RSS_WITH_CONTENT))
    articles = run(extractor.list_articles(FEED_URL))
    assert len(articles) == 1
    # content:encoded body is long; summary "Short summary." alone would fail make_article
    assert LONG_BODY.strip() in articles[0].text


def test_list_articles_skips_short_entries():
    extractor = RSSImportExtractor(_client=_client_returning(RSS_SHORT_ENTRIES))
    articles = run(extractor.list_articles(FEED_URL))
    assert articles == []


def test_list_articles_empty_feed_returns_empty_list():
    extractor = RSSImportExtractor(_client=_client_returning(RSS_EMPTY))
    articles = run(extractor.list_articles(FEED_URL))
    assert articles == []


# --- list_articles: failure modes ---------------------------------------------


def test_list_articles_http_error_raises():
    extractor = RSSImportExtractor(
        _client=_client_raising(httpx.ConnectError("timeout"))
    )
    with pytest.raises(ExtractionError, match="Could not fetch feed"):
        run(extractor.list_articles(FEED_URL))


def test_list_articles_http_status_error_raises():
    extractor = RSSImportExtractor(_client=_client_returning("Not Found", status=404))
    with pytest.raises(ExtractionError, match="Could not fetch feed"):
        run(extractor.list_articles(FEED_URL))


# --- extract ------------------------------------------------------------------


def test_extract_returns_first_article():
    extractor = RSSImportExtractor(_client=_client_returning(RSS_FEED))
    article = run(extractor.extract(FEED_URL))
    assert article.title == "First Post"


def test_extract_empty_feed_raises():
    extractor = RSSImportExtractor(_client=_client_returning(RSS_EMPTY))
    with pytest.raises(ExtractionError, match="Feed contains no usable entries"):
        run(extractor.extract(FEED_URL))
