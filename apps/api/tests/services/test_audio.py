import io
import math
import struct
import time
import wave

import pytest

from services.audio import (
    mulaw_wav_to_pcm16_wav,
    pcm16_to_mulaw_wav,
    wav_duration_seconds,
)


def pcm(samples: list[int]) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


def sine(freq: int, rate: int, seconds: float, amplitude: int = 10000) -> list[int]:
    return [
        int(amplitude * math.sin(2 * math.pi * freq * i / rate)) for i in range(int(rate * seconds))
    ]


def mulaw_data(wav: bytes) -> bytes:
    pos = wav.index(b"data")
    (size,) = struct.unpack("<I", wav[pos + 4 : pos + 8])
    return wav[pos + 8 : pos + 8 + size]


def decoded(wav: bytes) -> list[int]:
    with wave.open(io.BytesIO(mulaw_wav_to_pcm16_wav(wav))) as reader:
        frames = reader.readframes(reader.getnframes())
    return list(struct.unpack(f"<{len(frames) // 2}h", frames))


def test_header_fields_parse_back():
    wav = pcm16_to_mulaw_wav(pcm([0] * 3 * 101), 24000)  # 101 samples: odd data length
    assert wav[:4] == b"RIFF" and wav[8:16] == b"WAVEfmt "
    (riff_size,) = struct.unpack("<I", wav[4:8])
    assert riff_size == len(wav) - 8
    assert struct.unpack("<IHHIIHHH", wav[16:38]) == (18, 7, 1, 8000, 8000, 1, 8, 0)
    assert wav[38:42] == b"fact" and struct.unpack("<II", wav[42:50]) == (4, 101)
    assert wav[50:54] == b"data" and struct.unpack("<I", wav[54:58]) == (101,)
    assert len(wav) == 58 + 101 + 1  # pad byte after an odd data chunk


@pytest.mark.parametrize(
    ("sample", "byte"),
    [(0, 0xFF), (32767, 0x80), (-32768, 0x00), (-1, 0x7F), (1, 0xFF)],
)
def test_mulaw_known_values(sample: int, byte: int):
    assert mulaw_data(pcm16_to_mulaw_wav(pcm([sample]), 8000)) == bytes([byte])


def test_round_trip_error_is_small():
    samples = sine(440, 8000, 0.25, 20000)
    out = decoded(pcm16_to_mulaw_wav(pcm(samples), 8000))
    assert len(out) == len(samples)
    for original, back in zip(samples, out, strict=True):
        assert abs(original - back) <= max(abs(original) // 16, 40)


def test_24k_to_8k_sample_count_is_a_third():
    for n in (0, 1, 2, 3, 300, 301, 302):
        assert len(mulaw_data(pcm16_to_mulaw_wav(pcm([5] * n), 24000))) == n // 3


def test_a_1khz_sine_keeps_its_amplitude_through_decimation():
    out = decoded(pcm16_to_mulaw_wav(pcm(sine(1000, 24000, 0.5)), 24000))
    assert 7500 < max(abs(s) for s in out) < 11000


def test_non_integer_ratio_uses_interpolation():
    wav = pcm16_to_mulaw_wav(pcm(sine(500, 22050, 1.0)), 22050)
    assert len(mulaw_data(wav)) == 22050 * 8000 // 22050
    assert 8000 < max(abs(s) for s in decoded(wav)) < 11000


def test_an_odd_trailing_byte_is_dropped():
    assert mulaw_data(pcm16_to_mulaw_wav(pcm([1, 2, 3]) + b"\x07", 8000)) == mulaw_data(
        pcm16_to_mulaw_wav(pcm([1, 2, 3]), 8000)
    )


def test_empty_input_is_a_valid_empty_wav():
    wav = pcm16_to_mulaw_wav(b"", 24000)
    assert mulaw_data(wav) == b""
    assert struct.unpack("<I", wav[4:8]) == (len(wav) - 8,)
    assert decoded(wav) == []
    assert wav_duration_seconds(wav) == 0


def test_durations_for_both_formats():
    wav = pcm16_to_mulaw_wav(pcm(sine(300, 24000, 1.5)), 24000)
    assert wav_duration_seconds(wav) == pytest.approx(1.5)
    assert wav_duration_seconds(mulaw_wav_to_pcm16_wav(wav)) == pytest.approx(1.5)


def test_the_decoder_rejects_garbage_and_other_formats():
    for bad in (b"", b"not a wav at all", b"RIFF\x00\x00\x00\x00WAVE"):
        with pytest.raises(ValueError):
            mulaw_wav_to_pcm16_wav(bad)
    plain = io.BytesIO()
    with wave.open(plain, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(pcm([1, 2, 3]))
    with pytest.raises(ValueError):
        mulaw_wav_to_pcm16_wav(plain.getvalue())
    good = pcm16_to_mulaw_wav(pcm([1] * 30), 24000)
    with pytest.raises(ValueError):
        mulaw_wav_to_pcm16_wav(good[:-4])  # truncated data chunk


def test_ten_seconds_of_24khz_audio_converts_quickly():
    audio = pcm(sine(440, 24000, 10))
    started = time.perf_counter()
    wav = pcm16_to_mulaw_wav(audio, 24000)
    assert time.perf_counter() - started < 1.0
    assert len(mulaw_data(wav)) == 80000
