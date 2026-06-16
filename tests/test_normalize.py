import asyncio
import json

import httpx

from audibleweb.config import NormalizationConfig
from audibleweb.pipeline.normalize import _chunk_text, _is_configured, normalize_text

BASE_URL = "http://mock-llm"


def run(coro):
    return asyncio.run(coro)


def _llm_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
    )


def _config(*, enabled=True, base_url=BASE_URL, model="test-model", api_key=""):
    return NormalizationConfig(
        llm_enabled=enabled,
        llm_base_url=base_url,
        llm_model=model,
        llm_api_key=api_key,
    )


# --- _is_configured ---


def test_configured_when_all_set():
    assert _is_configured(_config()) is True


def test_not_configured_when_disabled():
    assert _is_configured(_config(enabled=False)) is False


def test_not_configured_when_no_base_url():
    assert _is_configured(_config(base_url="")) is False


def test_not_configured_when_no_model():
    assert _is_configured(_config(model="")) is False


# --- _chunk_text ---


def test_short_text_is_single_chunk():
    assert _chunk_text("hello world") == ["hello world"]


def test_chunks_split_on_paragraphs():
    # 3 × 800-char paragraphs: first two fit in 2000 chars (800+2+800=1602),
    # third would push to 1602+2+800=2404 so it spills to chunk 2.
    text = "a" * 800 + "\n\n" + "b" * 800 + "\n\n" + "c" * 800
    chunks = _chunk_text(text, max_chars=2000)
    assert len(chunks) == 2
    assert "a" in chunks[0] and "b" in chunks[0]
    assert "c" in chunks[1]


def test_single_paragraph_too_long_stays_one_chunk():
    long_para = "x" * 5000
    chunks = _chunk_text(long_para, max_chars=2000)
    assert len(chunks) == 1
    assert chunks[0] == long_para


# --- normalize_text: skip paths ---


def test_returns_original_when_disabled():
    cfg = _config(enabled=False)
    result = run(normalize_text("hello", cfg))
    assert result == "hello"


def test_returns_original_when_unconfigured_base_url():
    cfg = _config(base_url="")
    result = run(normalize_text("hello", cfg))
    assert result == "hello"


def test_returns_original_when_unconfigured_model():
    cfg = _config(model="")
    result = run(normalize_text("hello", cfg))
    assert result == "hello"


# --- normalize_text: LLM call ---


def test_normalizes_text_via_llm():
    responses = iter([_llm_response("forty-two")])
    transport = httpx.MockTransport(lambda req: next(responses))
    client = httpx.AsyncClient(transport=transport)

    result = run(normalize_text("42", _config(), _client=client))
    assert result == "forty-two"


def test_multi_chunk_joined_with_double_newline():
    chunks_received = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        user_msg = body["messages"][1]["content"]
        chunks_received.append(user_msg)
        return _llm_response(user_msg.upper())

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)

    text = "a" * 1500 + "\n\n" + "b" * 1500
    result = run(normalize_text(text, _config(), _client=client))
    assert len(chunks_received) == 2
    assert result == "\n\n".join(c.upper() for c in chunks_received)


# --- normalize_text: graceful degradation ---


def test_returns_original_on_http_error():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)

    text = "API call failed"
    result = run(normalize_text(text, _config(), _client=client))
    assert result == text


def test_returns_original_on_empty_llm_content():
    transport = httpx.MockTransport(lambda req: _llm_response(""))
    client = httpx.AsyncClient(transport=transport)

    text = "some article text"
    result = run(normalize_text(text, _config(), _client=client))
    assert result == text


def test_returns_original_on_malformed_response():
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"bad": "data"})
    )
    client = httpx.AsyncClient(transport=transport)

    text = "some text"
    result = run(normalize_text(text, _config(), _client=client))
    assert result == text


def test_auth_header_sent_when_api_key_set():
    received_headers = {}

    def handler(req: httpx.Request) -> httpx.Response:
        received_headers.update(dict(req.headers))
        return _llm_response("normalized")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)

    run(normalize_text("text", _config(api_key="sk-secret"), _client=client))
    assert received_headers.get("authorization") == "Bearer sk-secret"


def test_no_auth_header_when_key_is_ollama():
    received_headers = {}

    def handler(req: httpx.Request) -> httpx.Response:
        received_headers.update(dict(req.headers))
        return _llm_response("normalized")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)

    run(normalize_text("text", _config(api_key="ollama"), _client=client))
    assert "authorization" not in received_headers
