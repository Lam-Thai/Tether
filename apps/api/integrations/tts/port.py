"""The text-to-speech port: turn one line of agent text into raw audio.

Plain values in and out; no SDK type leaves this package. `GeminiTtsClient` is the real
adapter (Gemini's TTS model through google-genai), `FakeTtsClient` the scriptable
in-memory one for tests. Converting the audio to what Twilio `<Play>` accepts, caching
it and storing it are `services/speech.py`'s job, not the adapter's.

One `synthesize` is one provider attempt. Retrying is the caller's job, with
`core.retry.retry_bounded` and `TTS_RETRY`, wrapped around `UsageGuard.metered(TTS_REQUEST)`
so every attempt is reserved and counted on its own. Synthesis has no side effect, so a
timeout is as retryable as a 429 or a 5xx.
"""

from dataclasses import dataclass
from typing import Protocol

from core.retry import RetryPolicy

# At most 2 attempts per line, retrying only `retryable` failures. The caller also bounds
# the whole synthesis by a deadline, so a slow first attempt leaves no room for a second.
TTS_RETRY = RetryPolicy(attempts=2, backoff_seconds=0.2)


class TtsUnavailableError(Exception):
    """The provider produced no audio: timeout, network, quota, a refused or empty
    response, or no API key configured. The message is a fixed reason, never provider
    text. `retryable` is true only for timeouts, transport errors and HTTP 408/429/5xx;
    `rate_limited` only for HTTP 429."""

    def __init__(self, message: str, *, retryable: bool = False, rate_limited: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.rate_limited = rate_limited


def is_retryable(exc: Exception) -> bool:
    return isinstance(exc, TtsUnavailableError) and exc.retryable


@dataclass(frozen=True)
class SpeechAudio:
    """Mono, signed 16-bit little-endian PCM at `sample_rate` Hz."""

    pcm: bytes
    sample_rate: int

    def __repr__(self) -> str:  # never dump audio into a log line
        return f"SpeechAudio(bytes={len(self.pcm)}, sample_rate={self.sample_rate})"


class TtsClient(Protocol):
    async def synthesize(self, text: str, voice: str) -> SpeechAudio:
        """Speak `text` in the prebuilt voice `voice`. Raises `TtsUnavailableError`."""
        ...
