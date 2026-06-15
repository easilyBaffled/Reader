import io

import pytest
from pydub import AudioSegment

from audibleweb.lib.voice import (
    InvalidVoiceSpecError,
    VoiceSpec,
    VoiceWeight,
    mix_weighted_blend,
    parse_voice_spec,
)


def _silent_wav_bytes(duration_ms: int = 500, frame_rate: int = 24000) -> bytes:
    segment = AudioSegment.silent(duration=duration_ms, frame_rate=frame_rate)
    buf = io.BytesIO()
    segment.export(buf, format="wav")
    return buf.getvalue()


class TestParseVoiceSpec:
    def test_single_voice(self):
        result = parse_voice_spec("af_heart")
        assert result == VoiceSpec(
            type="native",
            voices=[VoiceWeight(name="af_heart", weight=1.0)],
            native_string="af_heart",
        )

    def test_native_two_way_blend(self):
        result = parse_voice_spec("af_heart+af_bella")
        assert result == VoiceSpec(
            type="native",
            voices=[
                VoiceWeight(name="af_heart", weight=0.5),
                VoiceWeight(name="af_bella", weight=0.5),
            ],
            native_string="af_heart+af_bella",
        )

    def test_native_three_way_blend(self):
        result = parse_voice_spec("af_heart+af_bella+am_puck")
        assert result == VoiceSpec(
            type="native",
            voices=[
                VoiceWeight(name="af_heart", weight=1 / 3),
                VoiceWeight(name="af_bella", weight=1 / 3),
                VoiceWeight(name="am_puck", weight=1 / 3),
            ],
            native_string="af_heart+af_bella+am_puck",
        )

    def test_weighted_two_way(self):
        result = parse_voice_spec("af_heart:0.7+af_bella:0.3")
        assert result == VoiceSpec(
            type="weighted",
            voices=[
                VoiceWeight(name="af_heart", weight=0.7),
                VoiceWeight(name="af_bella", weight=0.3),
            ],
            native_string=None,
        )

    def test_weighted_equal_split(self):
        result = parse_voice_spec("af_heart:0.5+af_bella:0.5")
        assert result == VoiceSpec(
            type="weighted",
            voices=[
                VoiceWeight(name="af_heart", weight=0.5),
                VoiceWeight(name="af_bella", weight=0.5),
            ],
            native_string=None,
        )

    def test_valid_native_blend_does_not_raise(self):
        parse_voice_spec("af_heart+am_puck")

    def test_invalid_weights_dont_sum(self):
        with pytest.raises(InvalidVoiceSpecError, match="sum"):
            parse_voice_spec("af_heart:0.7+af_bella:0.5")

    def test_invalid_weighted_three_voices(self):
        with pytest.raises(InvalidVoiceSpecError, match="2"):
            parse_voice_spec("af_heart:0.5+af_bella:0.3+am_puck:0.2")

    def test_invalid_native_four_voices(self):
        with pytest.raises(InvalidVoiceSpecError, match="3"):
            parse_voice_spec("a+b+c+d")

    def test_invalid_empty(self):
        with pytest.raises(InvalidVoiceSpecError):
            parse_voice_spec("")

    def test_invalid_bad_chars(self):
        with pytest.raises(InvalidVoiceSpecError):
            parse_voice_spec("af heart!")


class TestMixWeightedBlend:
    def test_full_weight_no_change(self):
        wav = _silent_wav_bytes()
        result = mix_weighted_blend(wav, 1.0, wav, 1.0)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_zero_weight_raises(self):
        wav = _silent_wav_bytes()
        with pytest.raises(ValueError):
            mix_weighted_blend(wav, 0.0, wav, 1.0)

    def test_mixes_two_equal_length_buffers(self):
        wav_a = _silent_wav_bytes(duration_ms=500)
        wav_b = _silent_wav_bytes(duration_ms=500)

        result = mix_weighted_blend(wav_a, 0.7, wav_b, 0.3)

        mixed = AudioSegment.from_wav(io.BytesIO(result))
        assert len(mixed) == 500

    def test_pads_shorter_buffer_to_match_longer(self):
        wav_a = _silent_wav_bytes(duration_ms=500)
        wav_b = _silent_wav_bytes(duration_ms=200)

        result = mix_weighted_blend(wav_a, 0.6, wav_b, 0.4)

        mixed = AudioSegment.from_wav(io.BytesIO(result))
        assert len(mixed) == 500
