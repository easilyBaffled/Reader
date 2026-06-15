"""Kokoro TTS engine: OpenAI-compatible /v1 audio API (docs/design.md sec 2.2, 4).

Vendored/adapted from audiobook-creator's generate_audio_with_retry +
generate_audio_for_voice (utils/llm_utils.py, generate_audiobook.py), split per
reader-8f2.4:

- `_generate_with_retry` is the shared retry/backoff helper for ALL voice
  paths (D8): one call for a native voice/blend, two calls (one per leg) for
  a weighted blend.
- Weighted-blend mixing delegates to lib/voice.py's `mix_weighted_blend`
  (reader-tt4) -- this module only knows how to talk to the TTS API.
- Timeout is 120s/chunk (docs/design.md sec 9), not audiobook-creator's 600s --
  AudibleWeb counts a timeout as a retryable chunk failure, not a long wait.
"""

from __future__ import annotations

import asyncio
import random

import httpx

from audibleweb.lib.voice import mix_weighted_blend, parse_voice_spec

MAX_RETRIES = 3
BASE_DELAY_SEC = 0.1
MAX_DELAY_SEC = 10.0
REQUEST_TIMEOUT_SEC = 120.0


class KokoroEngineError(Exception):
    """Raised when the TTS API request fails after all retries (docs/design.md sec 9)."""


class KokoroEngine:
    name = "kokoro"
    supports_blending = True

    def __init__(
        self,
        base_url: str,
        model: str = "kokoro",
        api_key: str = "not-needed",
        max_parallel: int = 4,
        client: httpx.AsyncClient | None = None,
    ):
        self._model = model
        self._semaphore = asyncio.Semaphore(max_parallel)
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=REQUEST_TIMEOUT_SEC,
        )

    async def synthesize(self, text: str, voice: str, speed: float = 1.0) -> bytes:
        spec = parse_voice_spec(voice)

        if spec.type == "native":
            return await self._generate_with_retry(text, spec.native_string, speed)

        voice_a, voice_b = spec.voices
        buffer_a, buffer_b = await asyncio.gather(
            self._generate_with_retry(text, voice_a.name, speed),
            self._generate_with_retry(text, voice_b.name, speed),
        )
        return mix_weighted_blend(buffer_a, voice_a.weight, buffer_b, voice_b.weight)

    async def list_voices(self) -> list[str]:
        response = await self._client.get("/audio/voices")
        response.raise_for_status()
        return response.json()["voices"]

    async def _generate_with_retry(self, text: str, voice: str, speed: float) -> bytes:
        """Generate one voice's audio, retrying with exponential backoff + jitter.

        Total attempts: 1 + MAX_RETRIES. Error discrimination: none -- any
        exception (HTTP error, timeout, connection failure) is retried
        identically, matching audiobook-creator's contract.
        """
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                async with self._semaphore:
                    response = await self._client.post(
                        "/audio/speech",
                        json={
                            "model": self._model,
                            "voice": voice,
                            "input": text,
                            "response_format": "wav",
                            "speed": speed,
                        },
                    )
                    response.raise_for_status()
                    return response.content
            except Exception as exc:
                last_error = exc
                if attempt < MAX_RETRIES:
                    delay = min(BASE_DELAY_SEC * (2**attempt), MAX_DELAY_SEC)
                    await asyncio.sleep(delay + random.uniform(0, 0.1) * delay)

        raise KokoroEngineError(
            f"TTS request for voice {voice!r} failed after "
            f"{MAX_RETRIES + 1} attempts: {last_error}"
        ) from last_error

    async def aclose(self) -> None:
        await self._client.aclose()
