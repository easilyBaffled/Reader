"""TTS engine plugin protocol (docs/design.md sec 2.2).

Deviates from docs/design.md sec 2.2 in two ways, both decided alongside
reader-8f2.4:

- `synthesize` returns raw WAV `bytes`, not a pydub `AudioSegment`. This keeps
  the bytes-in/bytes-out contract started by lib/voice.py's
  `mix_weighted_blend` (reader-tt4) and lets pipeline/stitch.py (reader-8f2.5)
  write chunks straight to disk for FFmpeg concat without pulling pydub into
  the stitching path (CLAUDE.md: "FFmpeg for stitching, not PyDub").
- `list_voices` returns `list[str]`, not `list[Voice]` -- the Kokoro
  `/audio/voices` endpoint returns a flat list of voice-id strings
  (`{"voices": ["af_heart", ...]}`); no other engine exists yet to justify a
  richer `Voice` type.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TTSEngine(Protocol):
    name: str
    supports_blending: bool

    async def synthesize(self, text: str, voice: str, speed: float = 1.0) -> bytes:
        """Synthesize `text` as `voice` (a voice spec string, see lib/voice.py)
        at `speed`. Returns WAV audio bytes."""
        ...

    async def list_voices(self) -> list[str]:
        """List voice IDs available from this engine."""
        ...
