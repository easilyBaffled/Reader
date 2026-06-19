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
from collections.abc import Awaitable, Callable

import httpx

from audibleweb.lib.voice import VoiceSpec, mix_weighted_blend, parse_voice_spec

MAX_RETRIES = 3
BASE_DELAY_SEC = 0.1
MAX_DELAY_SEC = 10.0
REQUEST_TIMEOUT_SEC = 120.0

_RIFF = b"RIFF"
_WAVE = b"WAVE"


class KokoroEngineError(Exception):
    """Raised when the TTS API request fails after all retries (docs/design.md sec 9)."""


class InvalidWAVError(Exception):
    """Raised when TTS response has an invalid RIFF/WAVE header."""


def _validate_wav_header(data: bytes) -> None:
    if len(data) < 12 or data[:4] != _RIFF or data[8:12] != _WAVE:
        raise InvalidWAVError(
            f"Invalid WAV header: expected RIFF....WAVE, got {data[:12]!r}"
        )


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

    async def synthesize(
        self,
        text: str,
        voice: str,
        speed: float = 1.0,
        *,
        check_cancel: Callable[[], Awaitable[None]] | None = None,
        on_retry: Callable[[int, Exception], None] | None = None,
    ) -> bytes:
        spec = parse_voice_spec(voice)

        if spec.type == "native":
            result = await self._generate_with_retry(
                text, spec.native_string, speed, on_retry=on_retry
            )
        else:
            result = await self._synthesize_weighted(text, spec, speed, on_retry=on_retry)

        if check_cancel is not None:
            await check_cancel()

        return result

    async def _synthesize_weighted(
        self,
        text: str,
        spec: VoiceSpec,
        speed: float,
        *,
        on_retry: Callable[[int, Exception], None] | None = None,
    ) -> bytes:
        """Synthesize a weighted blend; falls back to the surviving voice if one leg fails."""
        voice_a, voice_b = spec.voices
        raw = await asyncio.gather(
            self._generate_with_retry(text, voice_a.name, speed, on_retry=on_retry),
            self._generate_with_retry(text, voice_b.name, speed, on_retry=on_retry),
            return_exceptions=True,
        )
        buf_a: bytes | BaseException = raw[0]
        buf_b: bytes | BaseException = raw[1]

        a_ok = not isinstance(buf_a, Exception)
        b_ok = not isinstance(buf_b, Exception)

        if a_ok and b_ok:
            return mix_weighted_blend(buf_a, voice_a.weight, buf_b, voice_b.weight)  # type: ignore[arg-type]
        if a_ok:
            return buf_a  # type: ignore[return-value]
        if b_ok:
            return buf_b  # type: ignore[return-value]
        cause = buf_a if isinstance(buf_a, Exception) else None
        raise KokoroEngineError(
            f"Both voices in weighted blend failed: {buf_a}; {buf_b}"
        ) from cause

    async def list_voices(self) -> list[str]:
        response = await self._client.get("/audio/voices")
        response.raise_for_status()
        return response.json()["voices"]

    async def _generate_with_retry(
        self,
        text: str,
        voice: str,
        speed: float,
        *,
        on_retry: Callable[[int, Exception], None] | None = None,
    ) -> bytes:
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
                    data = response.content
                    _validate_wav_header(data)
                    return data
            except Exception as exc:
                last_error = exc
                if attempt < MAX_RETRIES:
                    if on_retry is not None:
                        on_retry(attempt, exc)
                    delay = min(BASE_DELAY_SEC * (2**attempt), MAX_DELAY_SEC)
                    await asyncio.sleep(delay + random.uniform(0, 0.1) * delay)

        raise KokoroEngineError(
            f"TTS request for voice {voice!r} failed after "
            f"{MAX_RETRIES + 1} attempts: {last_error}"
        ) from last_error

    async def aclose(self) -> None:
        await self._client.aclose()
