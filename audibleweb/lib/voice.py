"""Voice spec parsing/validation + weighted-blend audio mixing.

Vendored + split from audiobook-creator's utils/{voice_parser,audio_mixer}.py
(reader-tt4 / Eng D1): mix_weighted_blend() is pure bytes-in/bytes-out with no
TTS API calls. The TTS engine (engines/kokoro.py, reader-8f2.4) generates each
voice's audio separately and is the only thing that knows about TTS clients;
this module stays synthesis-agnostic.

Voice spec syntax:
- "af_heart"                  -> single voice (native, 1-way)
- "af_heart+af_bella"         -> native blend, <=3 voices, equal weights
                                  (native_string passed straight to the TTS API)
- "af_heart:0.7+af_bella:0.3" -> weighted blend, exactly 2 voices, weights sum
                                  to 1.0 (each voice synthesized separately,
                                  mixed via mix_weighted_blend)
"""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass
from typing import Literal

from pydub import AudioSegment

_VOICE_STR_RE = re.compile(r"^[a-zA-Z0-9_+:.]+$")
_VOICE_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+$")


class InvalidVoiceSpecError(ValueError):
    """Raised by parse_voice_spec() when a voice spec string fails validation."""


@dataclass(frozen=True)
class VoiceWeight:
    name: str
    weight: float


@dataclass(frozen=True)
class VoiceSpec:
    type: Literal["native", "weighted"]
    voices: list[VoiceWeight]
    native_string: str | None  # original string for native blends; None for weighted


def parse_voice_spec(voice_str: str) -> VoiceSpec:
    """Parse + validate a voice spec string.

    Raises InvalidVoiceSpecError with a human-readable message if voice_str
    doesn't match the syntax rules described in the module docstring.
    """
    _validate(voice_str)
    if ":" in voice_str:
        return _parse_weighted(voice_str)
    return _parse_native(voice_str)


def _validate(voice_str: str) -> None:
    if not voice_str or not voice_str.strip():
        raise InvalidVoiceSpecError("Voice spec cannot be empty")

    if not _VOICE_STR_RE.match(voice_str):
        raise InvalidVoiceSpecError(
            "Voice names can only contain alphanumeric characters and underscores"
        )

    if ":" in voice_str:
        _validate_weighted(voice_str)
    else:
        _validate_native(voice_str)


def _validate_weighted(voice_str: str) -> None:
    parts = voice_str.split("+")
    if len(parts) > 2:
        raise InvalidVoiceSpecError("Weighted blends support maximum 2 voices")

    total_weight = 0.0
    for part in parts:
        if ":" not in part:
            raise InvalidVoiceSpecError(
                "Weighted blend must specify weights for all voices"
            )

        name, weight_str = part.split(":", 1)
        if not _VOICE_NAME_RE.match(name.strip()):
            raise InvalidVoiceSpecError(
                "Voice names can only contain alphanumeric characters and underscores"
            )

        try:
            weight = float(weight_str.strip())
        except ValueError as e:
            raise InvalidVoiceSpecError(f"Invalid weight value: {weight_str}") from e

        if weight < 0 or weight > 1:
            raise InvalidVoiceSpecError("Weights must be between 0 and 1")
        total_weight += weight

    if abs(total_weight - 1.0) > 0.01:
        raise InvalidVoiceSpecError(f"Weights must sum to 1.0 (got {total_weight})")


def _validate_native(voice_str: str) -> None:
    names = voice_str.split("+")
    if len(names) > 3:
        raise InvalidVoiceSpecError("Native blends support maximum 3 voices")

    for name in names:
        if not _VOICE_NAME_RE.match(name.strip()):
            raise InvalidVoiceSpecError(
                "Voice names can only contain alphanumeric characters and underscores"
            )


def _parse_weighted(voice_str: str) -> VoiceSpec:
    voices = []
    for part in voice_str.split("+"):
        name, weight_str = part.split(":", 1)
        voices.append(VoiceWeight(name=name.strip(), weight=float(weight_str.strip())))
    return VoiceSpec(type="weighted", voices=voices, native_string=None)


def _parse_native(voice_str: str) -> VoiceSpec:
    names = [name.strip() for name in voice_str.split("+")]
    equal_weight = 1.0 / len(names)
    voices = [VoiceWeight(name=name, weight=equal_weight) for name in names]
    return VoiceSpec(type="native", voices=voices, native_string=voice_str)


def mix_weighted_blend(
    buffer_a: bytes, weight_a: float, buffer_b: bytes, weight_b: float
) -> bytes:
    """Mix two WAV audio buffers by weight.

    Pure bytes-in/bytes-out, no TTS calls (Eng D1) -- the engine generates each
    voice's audio separately and passes the resulting WAV bytes here to mix.
    """
    seg_a = _wav_bytes_to_segment(buffer_a)
    seg_b = _wav_bytes_to_segment(buffer_b)

    len_a, len_b = len(seg_a), len(seg_b)
    if len_a > len_b:
        seg_b += AudioSegment.silent(
            duration=len_a - len_b, frame_rate=seg_b.frame_rate
        )
    elif len_b > len_a:
        seg_a += AudioSegment.silent(
            duration=len_b - len_a, frame_rate=seg_a.frame_rate
        )

    seg_a = _adjust_volume_by_weight(seg_a, weight_a)
    seg_b = _adjust_volume_by_weight(seg_b, weight_b)

    return _segment_to_wav_bytes(seg_a.overlay(seg_b))


def _adjust_volume_by_weight(segment: AudioSegment, weight: float) -> AudioSegment:
    """weight=1.0 -> unchanged; weight=0.5 -> ~6dB reduction."""
    if weight <= 0:
        raise ValueError("Weight must be greater than 0")
    if weight >= 1.0:
        return segment
    return segment + (20 * math.log10(weight))


def _wav_bytes_to_segment(wav_bytes: bytes) -> AudioSegment:
    return AudioSegment.from_wav(io.BytesIO(wav_bytes))


def _segment_to_wav_bytes(segment: AudioSegment) -> bytes:
    buf = io.BytesIO()
    segment.export(buf, format="wav")
    return buf.getvalue()
