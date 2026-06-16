"""Tests for WebExtractor (audibleweb/extractors/web.py)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from audibleweb.extractors.base import ExtractionError
from audibleweb.extractors.web import WebExtractor

LONG_TEXT = "This is a sufficiently long article body for extraction tests. " * 3
SHORT_TEXT = "Too short."
FAKE_HTML = "<html><body><article>Some article content here.</article></body></html>"
TARGET_URL = "http://example.com/article"
JINA_URL = "https://r.jina.ai/" + TARGET_URL


def run(coro):
    return asyncio.run(coro)


def _mock_doc(
    text=LONG_TEXT, title="Article Title", author="Jane Doe", date="2024-01-15"
):
    return SimpleNamespace(text=text, title=title, author=author, date=date)


def _client_returning(
    url_to_body: dict[str, str], status: int = 200
) -> httpx.AsyncClient:
    """Build a mock httpx.AsyncClient that returns fixed bodies by URL prefix."""

    def handler(request: httpx.Request) -> httpx.Response:
        for prefix, body in url_to_body.items():
            if str(request.url).startswith(prefix):
                return httpx.Response(status, text=body)
        return httpx.Response(404, text="Not found")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _client_raising(exc: Exception) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- can_handle ---------------------------------------------------------------


def test_can_handle_http_url():
    assert WebExtractor().can_handle("http://example.com") is True


def test_can_handle_https_url():
    assert WebExtractor().can_handle("https://example.com/path") is True


def test_cannot_handle_plain_text():
    assert WebExtractor().can_handle("just some text") is False


def test_cannot_handle_file_path():
    assert WebExtractor().can_handle("/path/to/file.txt") is False


# --- trafilatura success path -------------------------------------------------


def test_extract_trafilatura_success():
    client = _client_returning({TARGET_URL: FAKE_HTML})
    extractor = WebExtractor(jina_fallback=False, _client=client)
    doc = _mock_doc()

    with patch("trafilatura.extract", return_value=doc):
        article = run(extractor.extract(TARGET_URL))

    assert article.text == LONG_TEXT.strip()
    assert article.title == "Article Title"
    assert article.author == "Jane Doe"
    assert article.source_url == TARGET_URL
    assert article.published is not None
    assert article.published.year == 2024


def test_extract_trafilatura_no_metadata_uses_derived_title():
    client = _client_returning({TARGET_URL: FAKE_HTML})
    extractor = WebExtractor(jina_fallback=False, _client=client)
    doc = SimpleNamespace(text=LONG_TEXT, title=None, author=None, date=None)

    with patch("trafilatura.extract", return_value=doc):
        article = run(extractor.extract(TARGET_URL))

    assert article.title  # derived from first line of LONG_TEXT


# --- Jina fallback -----------------------------------------------------------


def test_extract_falls_back_to_jina_when_trafilatura_short():
    client = _client_returning({TARGET_URL: FAKE_HTML, JINA_URL: LONG_TEXT})
    extractor = WebExtractor(jina_fallback=True, _client=client)
    short_doc = _mock_doc(text=SHORT_TEXT)

    with patch("trafilatura.extract", return_value=short_doc):
        article = run(extractor.extract(TARGET_URL))

    assert article.text == LONG_TEXT.strip()
    assert article.source_url == TARGET_URL


def test_extract_falls_back_to_jina_when_trafilatura_returns_none():
    client = _client_returning({TARGET_URL: FAKE_HTML, JINA_URL: LONG_TEXT})
    extractor = WebExtractor(jina_fallback=True, _client=client)

    with patch("trafilatura.extract", return_value=None):
        article = run(extractor.extract(TARGET_URL))

    assert LONG_TEXT.strip() in article.text


def test_extract_jina_sends_api_key_header():
    seen_headers: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(JINA_URL):
            seen_headers.append(dict(request.headers))
            return httpx.Response(200, text=LONG_TEXT)
        return httpx.Response(200, text=FAKE_HTML)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    extractor = WebExtractor(jina_fallback=True, jina_api_key="mykey", _client=client)

    with patch("trafilatura.extract", return_value=None):
        run(extractor.extract(TARGET_URL))

    assert any(h.get("authorization") == "Bearer mykey" for h in seen_headers)


# --- failure modes (design.md sec 9) ----------------------------------------


def test_unreachable_url_raises_could_not_fetch():
    client = _client_raising(httpx.ConnectError("timeout"))
    extractor = WebExtractor(jina_fallback=False, _client=client)

    with pytest.raises(ExtractionError, match="Could not fetch URL"):
        run(extractor.extract(TARGET_URL))


def test_http_error_raises_could_not_fetch():
    client = _client_returning({TARGET_URL: "Not Found"}, status=404)
    extractor = WebExtractor(jina_fallback=False, _client=client)

    with pytest.raises(ExtractionError, match="Could not fetch URL"):
        run(extractor.extract(TARGET_URL))


def test_no_content_without_jina_raises_no_extractable_content():
    client = _client_returning({TARGET_URL: FAKE_HTML})
    extractor = WebExtractor(jina_fallback=False, _client=client)

    with patch("trafilatura.extract", return_value=None):
        with pytest.raises(ExtractionError, match="No extractable content"):
            run(extractor.extract(TARGET_URL))


def test_both_methods_fail_when_jina_returns_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(JINA_URL):
            return httpx.Response(500, text="server error")
        return httpx.Response(200, text=FAKE_HTML)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    extractor = WebExtractor(jina_fallback=True, _client=client)

    with patch("trafilatura.extract", return_value=None):
        with pytest.raises(
            ExtractionError, match="Extraction failed \\(both methods\\)"
        ):
            run(extractor.extract(TARGET_URL))


def test_both_methods_fail_when_jina_returns_short_content():
    client = _client_returning({TARGET_URL: FAKE_HTML, JINA_URL: SHORT_TEXT})
    extractor = WebExtractor(jina_fallback=True, _client=client)

    with patch("trafilatura.extract", return_value=None):
        with pytest.raises(
            ExtractionError, match="Extraction failed \\(both methods\\)"
        ):
            run(extractor.extract(TARGET_URL))
