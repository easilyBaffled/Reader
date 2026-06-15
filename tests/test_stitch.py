import asyncio
import json
import wave
from pathlib import Path

import pytest

from audibleweb.pipeline.stitch import StitchError, stitch_chunks


def run(coro):
    return asyncio.run(coro)


def _write_silence_wav(
    path: Path, duration_sec: float, sample_rate: int = 24000, channels: int = 1
) -> None:
    n_frames = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"\x00\x00" * channels * n_frames)


def test_stitch_chunks_concats_with_silence_padding(tmp_path):
    chunk1 = tmp_path / "chunk1.wav"
    chunk2 = tmp_path / "chunk2.wav"
    _write_silence_wav(chunk1, 1.0)
    _write_silence_wav(chunk2, 1.5)
    output = tmp_path / "episode.mp3"

    duration = run(stitch_chunks([chunk1, chunk2], output))

    assert output.exists()
    # 1.0 + 1.5 chunks + 0.5*2 silence padding = 3.5s; allow encoder delay/padding
    assert 3.4 < duration < 3.7


async def _probe_audio_stream(path: Path) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_name,bit_rate",
        "-of",
        "json",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return json.loads(stdout)["streams"][0]


def test_stitch_chunks_output_is_cbr_mp3(tmp_path):
    chunk = tmp_path / "chunk.wav"
    _write_silence_wav(chunk, 0.5)
    output = tmp_path / "episode.mp3"

    run(stitch_chunks([chunk], output))

    stream = run(_probe_audio_stream(output))
    assert stream["codec_name"] == "mp3"
    assert stream["bit_rate"] == "128000"


def test_stitch_chunks_handles_different_sample_rates(tmp_path):
    chunk1 = tmp_path / "chunk1.wav"
    chunk2 = tmp_path / "chunk2.wav"
    _write_silence_wav(chunk1, 1.0, sample_rate=24000)
    _write_silence_wav(chunk2, 1.0, sample_rate=22050)
    output = tmp_path / "episode.mp3"

    duration = run(stitch_chunks([chunk1, chunk2], output))

    assert output.exists()
    # 1.0 + 1.0 chunks + 0.5*2 silence padding = 3.0s
    assert 2.9 < duration < 3.2


def test_stitch_chunks_empty_list_raises():
    with pytest.raises(StitchError, match="No audio chunks"):
        run(stitch_chunks([], Path("/tmp/unused.mp3")))
