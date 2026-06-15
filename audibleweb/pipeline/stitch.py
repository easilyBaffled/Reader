"""Stitch per-chunk TTS audio into one episode MP3 via FFmpeg (docs/design.md sec 4).

FFmpeg concat (not PyDub, per CLAUDE.md - memory-efficient for long articles):
0.5s silence before the first chunk and after the last, no gap between chunks,
output as a single CBR 128kbps MP3. Duration is read back from the encoded
output file.

Eng D4: ffmpeg/ffprobe are invoked via asyncio.create_subprocess_exec with
hardcoded argv (no shell=True, no string interpolation) - the
run_shell_command_secure allowlist from audiobook-creator isn't needed beyond
that, since there's nothing here for it to validate.
"""

from __future__ import annotations

import asyncio
import json
import wave
from pathlib import Path

SILENCE_DURATION_SEC = 0.5
OUTPUT_BITRATE = "128k"


class StitchError(Exception):
    """Raised when ffmpeg/ffprobe fails to stitch chunks (docs/design.md sec 9)."""


async def stitch_chunks(chunk_paths: list[Path], output_path: Path) -> float:
    """Concat `chunk_paths` (WAV files) into a single MP3 at `output_path`.

    Adds SILENCE_DURATION_SEC of silence before the first chunk and after the
    last, with no gap between chunks. All streams are normalized to the first
    chunk's sample rate/channel layout before concatenation, so chunks (and
    the generated silence) don't need matching formats. Returns the output
    file's duration in seconds, read back via ffprobe.
    """
    if not chunk_paths:
        raise StitchError("No audio chunks to stitch")

    sample_rate, channels = _probe_wav_format(chunk_paths[0])
    channel_layout = "mono" if channels == 1 else "stereo"
    silence_input = [
        "-f",
        "lavfi",
        "-t",
        str(SILENCE_DURATION_SEC),
        "-i",
        f"anullsrc=r={sample_rate}:cl={channel_layout}",
    ]

    inputs: list[str] = [*silence_input]
    for chunk_path in chunk_paths:
        inputs += ["-i", str(chunk_path)]
    inputs += silence_input

    stream_count = len(chunk_paths) + 2
    aformat = f"aformat=sample_fmts=s16:sample_rates={sample_rate}:channel_layouts={channel_layout}"
    normalize = ";".join(f"[{i}:a]{aformat}[a{i}]" for i in range(stream_count))
    concat_inputs = "".join(f"[a{i}]" for i in range(stream_count))
    filter_complex = f"{normalize};{concat_inputs}concat=n={stream_count}:v=0:a=1[out]"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    await _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            "[out]",
            "-c:a",
            "libmp3lame",
            "-b:a",
            OUTPUT_BITRATE,
            str(output_path),
        ]
    )

    return await _probe_duration(output_path)


def _probe_wav_format(path: Path) -> tuple[int, int]:
    with wave.open(str(path), "rb") as wav_file:
        return wav_file.getframerate(), wav_file.getnchannels()


async def _run_ffmpeg(args: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise StitchError(f"ffmpeg failed: {stderr.decode().strip()}")


async def _probe_duration(path: Path) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise StitchError(f"ffprobe failed: {stderr.decode().strip()}")
    return float(json.loads(stdout)["format"]["duration"])
