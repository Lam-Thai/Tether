"""The speech-to-text port: the words a caller said in one recorded utterance.

Plain values in and out; no SDK type leaves this package. `GeminiSpeechToText` is the real
adapter (Gemini audio understanding through google-genai), `FakeSpeechToText` the
scriptable in-memory one for tests. Fetching and deleting the recording, metering,
redaction and the fallback are `services/speech_input.py`'s job, not the adapter's.

One `transcribe` is one provider attempt. Retrying is the caller's job, with
`core.retry.retry_bounded` and `STT_RETRY`, wrapped around
`UsageGuard.metered(STT_REQUEST)` so every attempt is reserved and counted on its own.
Transcription has no side effect, so a timeout is as retryable as a 429 or a 5xx.
"""

from dataclasses import dataclass
from typing import Protocol

from core.retry import RetryPolicy

# At most 2 attempts per utterance, retrying only `retryable` failures. The caller bounds
# the whole transcription by a deadline too, so a slow first attempt leaves no second.
STT_RETRY = RetryPolicy(attempts=2, backoff_seconds=0.2)


class SttUnavailableError(Exception):
    """The provider produced no transcript: timeout, network, quota, a blocked or empty
    response, or no API key configured. The message is a fixed reason, never provider text
    (and never the caller's words). `retryable` is true only for timeouts, transport errors
    and HTTP 408/429/5xx; `rate_limited` only for HTTP 429. Tokens the provider reported
    anyway (a blocked or truncated reply) ride along for metering."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        rate_limited: bool = False,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.rate_limited = rate_limited
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out


def is_retryable(exc: Exception) -> bool:
    return isinstance(exc, SttUnavailableError) and exc.retryable


@dataclass(frozen=True)
class TranscriptText:
    """What the caller said, verbatim (`""` when the recording holds no speech), plus the
    tokens the request used (audio counts as input)."""

    text: str
    tokens_in: int = 0
    tokens_out: int = 0

    def __repr__(self) -> str:  # never dump a caller's words into a log line
        return f"TranscriptText(chars={len(self.text)})"


class SpeechToText(Protocol):
    async def transcribe(self, audio: bytes, mime_type: str) -> TranscriptText:
        """Transcribe one recorded utterance. Raises `SttUnavailableError`."""
        ...
