"""In-memory `SpeechToText` for tests: scripted transcripts, recorded requests, failures."""

import asyncio

from integrations.stt.port import SttUnavailableError, TranscriptText

DEFAULT_TRANSCRIPT = "What are your opening hours?"
# What every fake request "uses", like `FakeLLMClient`'s 100 in / 20 out.
FAKE_TOKENS_IN = 100
FAKE_TOKENS_OUT = 10


class FakeSpeechToText:
    """`requests` holds `(bytes, mime_type)` per call (its length is the provider call
    count, failed calls included). `say(text)` queues the next transcripts (else
    `default`); `fail_next` queues errors raised by the next calls; `delay_seconds` makes
    every call slow, to test deadlines."""

    def __init__(self, *, default: str = DEFAULT_TRANSCRIPT, delay_seconds: float = 0.0) -> None:
        self.default = default
        self.delay_seconds = delay_seconds
        self.requests: list[tuple[int, str]] = []
        self._transcripts: list[str] = []
        self._failures: list[SttUnavailableError] = []

    def say(self, *texts: str) -> None:
        self._transcripts.extend(texts)

    def fail_next(self, error: SttUnavailableError, *, times: int = 1) -> None:
        self._failures.extend([error] * times)

    async def transcribe(self, audio: bytes, mime_type: str) -> TranscriptText:
        self.requests.append((len(audio), mime_type))
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self._failures:
            raise self._failures.pop(0)
        text = self._transcripts.pop(0) if self._transcripts else self.default
        return TranscriptText(text, tokens_in=FAKE_TOKENS_IN, tokens_out=FAKE_TOKENS_OUT)
