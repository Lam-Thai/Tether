"""Pure audio conversion for phone calls (stdlib only, no I/O).

Gemini TTS returns raw 16-bit PCM at 24 kHz. Twilio's `<Play>` takes a WAV, and the
format Twilio recommends for telephone audio is 8 kHz mono G.711 mu-law: it is the
phone network's native codec, so Twilio plays it with no transcoding on its side, and
at 8 bits x 8000 Hz it is the smallest file (a third of the 24 kHz PCM size, half of
8 kHz PCM16), which keeps the fetch and the stored cache small.

`mulaw_wav_to_pcm16_wav` turns the same file back into plain PCM16 for the browser
preview, since browsers do not reliably play mu-law WAV.
"""

import io
import struct
import wave
from collections.abc import Iterator

TARGET_RATE = 8000
_WAVE_FORMAT_PCM = 1
_WAVE_FORMAT_MULAW = 7
_BIAS = 0x84
_CLIP = 32635


def _encode_sample(sample: int) -> int:
    sign = 0x80 if sample < 0 else 0
    magnitude = min(-sample if sample < 0 else sample, _CLIP) + _BIAS
    exponent = (magnitude >> 7).bit_length() - 1
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def _decode_byte(byte: int) -> int:
    value = ~byte & 0xFF
    exponent = (value >> 4) & 0x07
    mantissa = value & 0x0F
    sample = (((mantissa << 3) + _BIAS) << exponent) - _BIAS
    return -sample if value & 0x80 else sample


# Indexed by the sample's unsigned 16-bit view (`sample & 0xFFFF`), built once.
_ENCODE_TABLE = bytes(_encode_sample(u - 65536 if u >= 32768 else u) for u in range(65536))
_DECODE_TABLE = tuple(_decode_byte(b) for b in range(256))


def _resample(samples: tuple[int, ...], from_rate: int, to_rate: int) -> list[int]:
    if from_rate == to_rate:
        return list(samples)
    if from_rate > to_rate and from_rate % to_rate == 0:
        k = from_rate // to_rate
        # Boxcar average of each group: a crude anti-alias filter, then decimation.
        return [sum(group) // k for group in zip(*(samples[i::k] for i in range(k)), strict=False)]
    count = len(samples) * to_rate // from_rate
    if count == 0:
        return []
    step = from_rate / to_rate
    last = len(samples) - 1
    out: list[int] = []
    for i in range(count):
        position = i * step
        index = int(position)
        if index >= last:
            out.append(samples[last])
            continue
        fraction = position - index
        out.append(int(samples[index] + (samples[index + 1] - samples[index]) * fraction))
    return out


def _chunk(tag: bytes, body: bytes) -> bytes:
    return tag + struct.pack("<I", len(body)) + body + (b"\0" if len(body) % 2 else b"")


def pcm16_to_mulaw_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Mono signed 16-bit little-endian PCM at any rate -> 8000 Hz mono mu-law WAV."""
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive.")
    count = len(pcm) // 2  # an odd trailing byte is dropped
    samples = struct.unpack(f"<{count}h", pcm[: count * 2])
    resampled = _resample(samples, sample_rate, TARGET_RATE)
    table = _ENCODE_TABLE
    data = bytes([table[s & 0xFFFF] for s in resampled])
    fmt = struct.pack("<HHIIHHH", _WAVE_FORMAT_MULAW, 1, TARGET_RATE, TARGET_RATE, 1, 8, 0)
    body = (
        b"WAVE"
        + _chunk(b"fmt ", fmt)
        + _chunk(b"fact", struct.pack("<I", len(data)))
        + _chunk(b"data", data)
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _chunks(data: bytes) -> Iterator[tuple[bytes, bytes]]:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("Not a WAV file.")
    pos = 12
    while pos + 8 <= len(data):
        tag = data[pos : pos + 4]
        (size,) = struct.unpack("<I", data[pos + 4 : pos + 8])
        body = data[pos + 8 : pos + 8 + size]
        if len(body) < size:
            raise ValueError("Truncated WAV chunk.")
        yield tag, body
        pos += 8 + size + (size % 2)


def _parse(data: bytes) -> tuple[int, int, int, int, bytes]:
    """(format tag, channels, sample rate, bits per sample, data) of a WAV."""
    fmt: tuple[int, int, int, int] | None = None
    audio: bytes | None = None
    for tag, body in _chunks(data):
        if tag == b"fmt " and len(body) >= 16:
            format_tag, channels, rate, _, _, bits = struct.unpack("<HHIIHH", body[:16])
            fmt = (format_tag, channels, rate, bits)
        elif tag == b"data":
            audio = body
    if fmt is None or audio is None:
        raise ValueError("WAV has no fmt or data chunk.")
    return (*fmt, audio)


def mulaw_wav_to_pcm16_wav(data: bytes) -> bytes:
    """Decode a mono 8 kHz mu-law WAV into a 16-bit PCM WAV at the same rate."""
    format_tag, channels, rate, bits, audio = _parse(data)
    if (format_tag, channels, rate, bits) != (_WAVE_FORMAT_MULAW, 1, TARGET_RATE, 8):
        raise ValueError("Not a mono 8 kHz mu-law WAV.")
    pcm = struct.pack(f"<{len(audio)}h", *[_DECODE_TABLE[b] for b in audio])
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(TARGET_RATE)
        writer.writeframes(pcm)
    return out.getvalue()


def wav_duration_seconds(data: bytes) -> float:
    """Length of a PCM16 or mu-law WAV in seconds."""
    format_tag, channels, rate, bits, audio = _parse(data)
    if format_tag not in (_WAVE_FORMAT_PCM, _WAVE_FORMAT_MULAW) or rate <= 0:
        raise ValueError("Unsupported WAV format.")
    frame = channels * max(bits // 8, 1)
    return len(audio) / frame / rate
