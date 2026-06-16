import asyncio
import io
import json

import httpx
import pytest
from pydub import AudioSegment

from audibleweb.engines.kokoro import (
    InvalidWAVError,
    KokoroEngine,
    KokoroEngineError,
    _validate_wav_header,
)
from audibleweb.lib.voice import InvalidVoiceSpecError

BASE_URL = "http://mock-tts/v1"


def run(coro):
    return asyncio.run(coro)


def _silent_wav_bytes(duration_ms: int = 100, frame_rate: int = 24000) -> bytes:
    segment = AudioSegment.silent(duration=duration_ms, frame_rate=frame_rate)
    buf = io.BytesIO()
    segment.export(buf, format="wav")
    return buf.getvalue()


SILENCE_WAV = _silent_wav_bytes()


def _mock_tts_handler(request: httpx.Request) -> httpx.Response:
    """Mock TTS server: /audio/speech -> silence WAV, /audio/voices -> voice list."""
    if request.url.path.endswith("/audio/speech"):
        return httpx.Response(200, content=SILENCE_WAV)
    if request.url.path.endswith("/audio/voices"):
        return httpx.Response(200, json={"voices": ["af_heart", "af_bella", "am_adam"]})
    return httpx.Response(404)


@pytest.fixture
def mock_tts_client() -> httpx.AsyncClient:
    transport = httpx.MockTransport(_mock_tts_handler)
    return httpx.AsyncClient(base_url=BASE_URL, transport=transport)


def _engine(client: httpx.AsyncClient, max_parallel: int = 4) -> KokoroEngine:
    return KokoroEngine(base_url=BASE_URL, client=client, max_parallel=max_parallel)


# --- synthesize: native voices/blends ----------------------------------------


def test_synthesize_single_voice_returns_wav_bytes(mock_tts_client):
    engine = _engine(mock_tts_client)
    result = run(engine.synthesize("Hello world", "af_heart"))
    assert result == SILENCE_WAV


def test_synthesize_native_blend_passes_combined_voice_string():
    seen_voices = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/audio/speech"):
            seen_voices.append(json.loads(request.read())["voice"])
            return httpx.Response(200, content=SILENCE_WAV)
        return httpx.Response(404)

    client = httpx.AsyncClient(
        base_url=BASE_URL, transport=httpx.MockTransport(handler)
    )
    engine = _engine(client)

    run(engine.synthesize("Hello world", "af_heart+af_bella"))

    assert seen_voices == ["af_heart+af_bella"]


def test_synthesize_invalid_voice_spec_raises_without_http_call():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, content=SILENCE_WAV)

    client = httpx.AsyncClient(
        base_url=BASE_URL, transport=httpx.MockTransport(handler)
    )
    engine = _engine(client)

    with pytest.raises(InvalidVoiceSpecError):
        run(engine.synthesize("Hello world", "not a valid spec!"))

    assert call_count == 0


# --- synthesize: weighted blend -----------------------------------------------


def test_synthesize_weighted_blend_mixes_both_voices():
    seen_voices = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/audio/speech"):
            seen_voices.append(json.loads(request.read())["voice"])
            return httpx.Response(200, content=SILENCE_WAV)
        return httpx.Response(404)

    client = httpx.AsyncClient(
        base_url=BASE_URL, transport=httpx.MockTransport(handler)
    )
    engine = _engine(client)

    result = run(engine.synthesize("Hello world", "af_heart:0.6+af_bella:0.4"))

    # both legs of the weighted blend were synthesized separately
    assert sorted(seen_voices) == ["af_bella", "af_heart"]

    # mix_weighted_blend's output is itself a valid WAV
    AudioSegment.from_wav(io.BytesIO(result))


# --- list_voices ---------------------------------------------------------------


def test_list_voices_returns_voice_ids(mock_tts_client):
    engine = _engine(mock_tts_client)
    voices = run(engine.list_voices())
    assert voices == ["af_heart", "af_bella", "am_adam"]


# --- _generate_with_retry: retry/backoff (D8) -----------------------------------


def test_generate_with_retry_retries_then_succeeds():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(500)
        return httpx.Response(200, content=SILENCE_WAV)

    client = httpx.AsyncClient(
        base_url=BASE_URL, transport=httpx.MockTransport(handler)
    )
    engine = _engine(client)

    result = run(engine.synthesize("Hello world", "af_heart"))

    assert attempts == 3
    assert result == SILENCE_WAV


def test_generate_with_retry_raises_after_exhausting_retries():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500)

    client = httpx.AsyncClient(
        base_url=BASE_URL, transport=httpx.MockTransport(handler)
    )
    engine = _engine(client)

    with pytest.raises(KokoroEngineError):
        run(engine.synthesize("Hello world", "af_heart"))

    # 1 initial attempt + 3 retries
    assert attempts == 4


# --- WAV header validation (reader-yau) ------------------------------------------


def test_validate_wav_header_accepts_valid_wav():
    _validate_wav_header(SILENCE_WAV)  # must not raise


def test_validate_wav_header_rejects_garbage():
    with pytest.raises(InvalidWAVError):
        _validate_wav_header(b"\x00" * 100)


def test_validate_wav_header_rejects_truncated():
    with pytest.raises(InvalidWAVError):
        _validate_wav_header(b"RIFF")  # only 4 bytes — missing WAVE at offset 8


def test_garbage_bytes_in_response_retries_then_raises():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, content=b"\x00" * 100)

    client = httpx.AsyncClient(
        base_url=BASE_URL, transport=httpx.MockTransport(handler)
    )
    engine = _engine(client)

    with pytest.raises(KokoroEngineError):
        run(engine.synthesize("Hello world", "af_heart"))

    assert attempts == 4  # 1 initial + 3 retries


def test_truncated_wav_bytes_retries_then_raises():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, content=b"RIFF")  # only 4 bytes, missing WAVE

    client = httpx.AsyncClient(
        base_url=BASE_URL, transport=httpx.MockTransport(handler)
    )
    engine = _engine(client)

    with pytest.raises(KokoroEngineError):
        run(engine.synthesize("Hello world", "af_heart"))

    assert attempts == 4


def test_invalid_wav_header_eventually_succeeds_on_retry():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if not request.url.path.endswith("/audio/speech"):
            return httpx.Response(404)
        attempts += 1
        if attempts < 3:
            return httpx.Response(200, content=b"notawave" * 10)
        return httpx.Response(200, content=SILENCE_WAV)

    client = httpx.AsyncClient(
        base_url=BASE_URL, transport=httpx.MockTransport(handler)
    )
    engine = _engine(client)

    result = run(engine.synthesize("Hello world", "af_heart"))

    assert attempts == 3
    assert result == SILENCE_WAV


# --- semaphore bounding (config tts.max_parallel) --------------------------------


def test_semaphore_bounds_concurrent_requests():
    concurrent = 0
    max_concurrent = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal concurrent, max_concurrent
        concurrent += 1
        max_concurrent = max(max_concurrent, concurrent)
        await asyncio.sleep(0.02)
        concurrent -= 1
        return httpx.Response(200, content=SILENCE_WAV)

    client = httpx.AsyncClient(
        base_url=BASE_URL, transport=httpx.MockTransport(handler)
    )
    engine = _engine(client, max_parallel=2)

    async def run_all():
        await asyncio.gather(
            *[engine.synthesize("chunk", "af_heart") for _ in range(4)]
        )

    run(run_all())

    assert max_concurrent == 2
