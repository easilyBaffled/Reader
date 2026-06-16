"""Optional LLM text normalization stage (docs/design.md sec 3, Stage 2).

Graceful degradation: if LLM is unconfigured, disabled, or returns an error,
normalize_text() returns the original text unchanged and logs a warning.
The pipeline continues; this stage never fails a job.

Chunking strategy: split by paragraph (double newline), group into batches of
at most MAX_CHUNK_CHARS chars (default 2000), normalize each batch
independently, then rejoin with the original separators.
"""

from __future__ import annotations

import json
import logging

import httpx

from audibleweb.config import NormalizationConfig

logger = logging.getLogger(__name__)

MAX_CHUNK_CHARS = 2000
REQUEST_TIMEOUT_SEC = 30.0

_SYSTEM_PROMPT = (
    "Rewrite for spoken narration. "
    "Expand abbreviations, spell out numbers, don't change meaning."
)


def _is_configured(config: NormalizationConfig) -> bool:
    return bool(
        config.llm_enabled and config.llm_base_url.strip() and config.llm_model.strip()
    )


def _chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split text into chunks of at most max_chars, preserving paragraph breaks."""
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current_parts: list[str] = []
    current_len = 0

    for para in paragraphs:
        addition = (len(current_parts) > 0) * 2 + len(para)
        if current_parts and current_len + addition > max_chars:
            chunks.append("\n\n".join(current_parts))
            current_parts = [para]
            current_len = len(para)
        else:
            current_parts.append(para)
            current_len += addition

    if current_parts:
        chunks.append("\n\n".join(current_parts))

    return chunks


def _build_url(base_url: str) -> str:
    url = base_url.rstrip("/")
    if url.lower().endswith("/v1"):
        return url + "/chat/completions"
    return url + "/v1/chat/completions"


async def _normalize_chunk(
    chunk: str,
    config: NormalizationConfig,
    client: httpx.AsyncClient,
) -> str:
    """Call LLM to normalize one chunk. Returns original chunk on any failure."""
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    token = config.llm_api_key.strip()
    if token and token.lower() != "ollama":
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "model": config.llm_model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": chunk},
        ],
        "temperature": 0.2,
    }

    try:
        response = await client.post(
            _build_url(config.llm_base_url),
            content=json.dumps(payload).encode(),
            headers=headers,
            timeout=REQUEST_TIMEOUT_SEC,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        logger.warning("LLM normalization failed, skipping: %s", exc)
        return chunk

    try:
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, str):
            content = content.strip()
        if not content:
            logger.warning("LLM returned empty content, skipping chunk")
            return chunk
        return content
    except (KeyError, IndexError, TypeError) as exc:
        logger.warning("LLM response malformed, skipping: %s", exc)
        return chunk


async def normalize_text(
    text: str,
    config: NormalizationConfig,
    *,
    _client: httpx.AsyncClient | None = None,
) -> str:
    """Normalize text via LLM for spoken narration.

    Returns the original text unchanged if LLM is not configured, disabled,
    or encounters any error (docs/design.md sec 9: graceful degradation).
    """
    if not _is_configured(config):
        return text

    chunks = _chunk_text(text)

    own_client = _client is None
    if own_client:
        _client = httpx.AsyncClient()

    try:
        normalized_chunks = []
        for chunk in chunks:
            result = await _normalize_chunk(chunk, config, _client)
            normalized_chunks.append(result)
    finally:
        if own_client:
            await _client.aclose()

    return "\n\n".join(normalized_chunks)
