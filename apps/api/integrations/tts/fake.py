"""In-memory `TtsClient` for tests: deterministic audio, recorded requests, scriptable failures."""

import asyncio
import math
import struct

from integrations.tts.port import SpeechAudio, TtsUnavailableError

SAMPLE_RATE = 24000
_SAMPLES_PER_CHAR = 480  # 20 ms of audio per character
_MAX_SAMPLES = SAMPLE_RATE * 30


def tone_pcm(text: str, voice: str) -> bytes:
    """A sine tone whose length scales with the text and whose pitch depends on the text
    and voice, so different requests give different bytes."""
    samples = min(max(len(text), 1) * _SAMPLES_PER_CHAR, _MAX_SAMPLES)
    freq = 300 + (sum(map(ord, text)) + sum(map(ord, voice))) % 500
    values = [int(8000 * math.sin(2 * math.pi * freq * i / SAMPLE_RATE)) for i in range(samples)]
    return struct.pack(f"<{samples}h", *values)


class FakeTtsClient:
    """`requests` holds `(text, voice)` per call (its length is the provider call count,
    failed calls included). `fail_next` queues errors raised by the next calls;
    `delay_seconds` makes every call slow, to test deadlines."""

    def __init__(self, *, delay_seconds: float = 0.0) -> None:
        self.delay_seconds = delay_seconds
        self.requests: list[tuple[str, str]] = []
        self._failures: list[TtsUnavailableError] = []

    def fail_next(self, error: TtsUnavailableError, *, times: int = 1) -> None:
        self._failures.extend([error] * times)

    async def synthesize(self, text: str, voice: str) -> SpeechAudio:
        self.requests.append((text, voice))
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self._failures:
            raise self._failures.pop(0)
        return SpeechAudio(pcm=tone_pcm(text, voice), sample_rate=SAMPLE_RATE)
