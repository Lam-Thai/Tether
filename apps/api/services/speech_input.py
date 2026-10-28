"""`SpeechRecognizer`: what a caller said, from a Twilio recording, which is then deleted.

    transcript = await recognizer.transcribe(recording, business_id=..., interaction_id=...)
    # `None`: reprompt and switch the call to Twilio's speech recognition.

Used when `VOICE_INPUT_MODE=gemini_audio` (slice 15b). Behind that small surface:

- The authenticated recording fetch (`integrations/twilio` `RecordingsApi`): the adapter
  builds the URL from the recording sid on our own account, with the credentials in the
  header only, so a webhook payload can never point our credentials somewhere else.
- Gemini audio understanding (`integrations/stt`, model id from settings) and `redact` on
  the transcript before anything stores it or shows it to the model.
- Metering: every Gemini attempt is `retry_bounded(STT_RETRY)` around
  `UsageGuard.metered(STT_REQUEST)` (capped; its tokens count as Gemini tokens), every
  Twilio call `retry_bounded(RECORDING_RETRY)` around `metered(TELEPHONY_API)` (metered
  only). The whole fetch + transcription runs under one deadline (`stt_timeout_seconds`,
  further capped by what the caller has left of Twilio's webhook timeout).
- Audio retention minimization (PRD §13.4): the recording is deleted at Twilio as soon as
  it is fetched (concurrently with the transcription, so it adds no latency), and on every
  other path too: a failed fetch, a reached limit, the deadline, no speech-to-text
  configured, even a cancelled request (the delete is shielded). A delete that still fails
  is logged as a fixed reason code and never drops the call; `sweep` (at the end of the
  call) removes anything left behind.

Every failure is `None`, logged as a fixed reason code with the elapsed ms on
`tether.speech_input`; never a guessed utterance. A silent recording, or one Gemini hears
no words in, is an empty `Transcript` (the call treats it as silence). Nothing here logs
audio or what the caller said.

No FastAPI or SQLAlchemy imports.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from core.retry import RetryPolicy, retry_bounded
from integrations.stt.port import STT_RETRY, SpeechToText, SttUnavailableError
from integrations.stt.port import is_retryable as stt_retryable
from integrations.twilio.port import (
    RECORDING_SID,
    RecordingAudio,
    RecordingsApi,
    TwilioApiError,
)
from integrations.twilio.port import is_retryable as twilio_retryable
from services.redaction import redact
from services.usage import LimitExceededError, UsageGuard, UsageKind, UsageUnavailableError

logger = logging.getLogger("tether.speech_input")

# Fetch, delete and list are idempotent: one more try on a transient failure.
RECORDING_RETRY = RetryPolicy(attempts=2, backoff_seconds=0.2)
# A caller utterance longer than this is cut (the same cap as Twilio's transcripts).
MAX_UTTERANCE_CHARS = 1000
# A WAV header, then 8 kHz 16-bit samples: under 0.1 s of audio is silence, not speech.
WAV_HEADER_BYTES = 44
MIN_SPEECH_BYTES = 1600
# The end-of-call sweep (list + deletes) never holds the status callback longer.
SWEEP_TIMEOUT_SECONDS = 5.0

# Deletions run as their own tasks so a cancelled request can't stop them. Handles only,
# so the event loop doesn't garbage-collect them mid-delete; never conversation state.
_DELETING: set[asyncio.Task[bool]] = set()


@dataclass(frozen=True)
class Recording:
    """A <Record> result from the input webhook (Twilio-signed): its sid and its call."""

    sid: str
    call_sid: str


@dataclass(frozen=True)
class Transcript:
    """The caller's words, redacted and whitespace-normalized (`""` = no speech), and how
    long the fetch and the transcription took (for the latency comparison)."""

    text: str
    fetch_ms: int
    stt_ms: int

    def __repr__(self) -> str:  # never dump a caller's words into a log line
        return f"Transcript(chars={len(self.text)}, fetch_ms={self.fetch_ms}, stt_ms={self.stt_ms})"


class SpeechRecognizer:
    def __init__(
        self,
        stt: SpeechToText | None,
        recordings: RecordingsApi | None,
        usage: UsageGuard,
        *,
        timeout_seconds: float,
        delete_timeout_seconds: float = 3.0,
    ) -> None:
        self._stt = stt
        self._recordings = recordings
        self._usage = usage
        self._timeout = timeout_seconds
        self._delete_timeout = delete_timeout_seconds

    @property
    def configured(self) -> bool:
        return self._stt is not None and self._recordings is not None

    @property
    def timeout_seconds(self) -> float:
        """The most one utterance's fetch + transcription may take."""
        return self._timeout

    async def transcribe(
        self,
        recording: Recording,
        *,
        business_id: uuid.UUID,
        interaction_id: uuid.UUID | None = None,
        deadline_seconds: float | None = None,
    ) -> Transcript | None:
        """The caller's words, or `None` (reprompt). Deletes the recording on every path.
        Never raises (cancellation aside, and the delete still happens then)."""
        if not RECORDING_SID.fullmatch(recording.sid):
            self._log("invalid_recording", business_id, interaction_id, 0, 0, 0.0)
            return None
        budget = self._timeout if deadline_seconds is None else min(self._timeout, deadline_seconds)
        started = time.monotonic()
        fetch_ms = stt_ms = 0
        outcome = "ok"
        deletion: asyncio.Task[bool] | None = None
        try:
            if self._stt is None or self._recordings is None:
                outcome = "not_configured"
                return None
            if budget <= 0:
                outcome = "no_time"
                return None
            async with asyncio.timeout(budget):
                audio = await self._fetch(recording.sid, business_id, interaction_id)
                fetch_ms = _ms_since(started)
                # Fetched: the recording isn't needed at Twilio any more.
                deletion = self._delete_in_background(recording.sid, business_id, interaction_id)
                if len(audio.data) - WAV_HEADER_BYTES < MIN_SPEECH_BYTES:
                    outcome = "no_speech"
                    return Transcript("", fetch_ms, 0)
                transcribing = time.monotonic()
                text = await self._words(audio, business_id, interaction_id)
                stt_ms = _ms_since(transcribing)
            if not text:
                outcome = "no_speech"
            return Transcript(text, fetch_ms, stt_ms)
        except LimitExceededError:
            outcome = "limit_reached"
        except UsageUnavailableError:
            outcome = "usage_unavailable"
        except SttUnavailableError as exc:
            outcome = "rate_limited" if exc.rate_limited else "provider_error"
        except TwilioApiError as exc:
            outcome = f"fetch_{exc.reason}"
        except TimeoutError:
            outcome = "timeout"
        except Exception as exc:
            # The type only: a provider or storage error's message can quote caller data.
            outcome = f"error:{type(exc).__name__}"
        finally:
            if deletion is None:
                deletion = self._delete_in_background(recording.sid, business_id, interaction_id)
            # Shielded: a cancelled request stops waiting, the delete keeps going.
            await asyncio.shield(deletion)
            self._log(outcome, business_id, interaction_id, fetch_ms, stt_ms, started)
        return None

    async def discard(
        self,
        recording: Recording,
        *,
        business_id: uuid.UUID | None,
        interaction_id: uuid.UUID | None = None,
    ) -> bool:
        """Deletes a recording that won't be transcribed (the call is over its time limit
        or already ended, the turn was already answered, recording mode is off). True when
        Twilio confirmed; never raises. `business_id=None` (no active call to attribute it
        to) meters the Twilio calls globally."""
        if not RECORDING_SID.fullmatch(recording.sid):
            return False
        return await asyncio.shield(
            self._delete_in_background(recording.sid, business_id, interaction_id)
        )

    async def sweep(
        self,
        call_sid: str,
        *,
        business_id: uuid.UUID,
        interaction_id: uuid.UUID | None = None,
    ) -> int:
        """Deletes every recording Twilio still holds for a finished call (left by a failed
        delete or a request that died mid-turn), all at once and within
        `SWEEP_TIMEOUT_SECONDS`, so the status callback stays fast. Returns how many were
        deleted; never raises."""
        recordings = self._recordings
        if recordings is None:
            return 0
        try:
            async with asyncio.timeout(SWEEP_TIMEOUT_SECONDS):
                sids = await retry_bounded(
                    lambda: self._metered_twilio(
                        lambda: recordings.list_for_call(call_sid), business_id, interaction_id
                    ),
                    RECORDING_RETRY,
                    twilio_retryable,
                )
                results = await asyncio.gather(
                    *(self._delete(sid, business_id, interaction_id) for sid in sids)
                )
        except Exception as exc:
            logger.warning(
                "recording sweep failed",
                extra={"business_id": str(business_id), "reason": _reason(exc)},
            )
            return 0
        deleted = sum(results)
        if sids:
            logger.info(
                "recordings swept",
                extra={"business_id": str(business_id), "found": len(sids), "deleted": deleted},
            )
        return deleted

    # --- steps -----------------------------------------------------------------------

    async def _fetch(
        self, sid: str, business_id: uuid.UUID, interaction_id: uuid.UUID | None
    ) -> RecordingAudio:
        recordings = self._recordings
        assert recordings is not None
        return await retry_bounded(
            lambda: self._metered_twilio(
                lambda: recordings.fetch(sid), business_id, interaction_id
            ),
            RECORDING_RETRY,
            twilio_retryable,
        )

    async def _words(
        self, audio: RecordingAudio, business_id: uuid.UUID, interaction_id: uuid.UUID | None
    ) -> str:
        transcript = await retry_bounded(
            lambda: self._attempt(audio, business_id, interaction_id), STT_RETRY, stt_retryable
        )
        return redact(" ".join(transcript.split()))[:MAX_UTTERANCE_CHARS].strip()

    async def _attempt(
        self, audio: RecordingAudio, business_id: uuid.UUID, interaction_id: uuid.UUID | None
    ) -> str:
        assert self._stt is not None
        async with self._usage.metered(
            UsageKind.STT_REQUEST, business_id=business_id, interaction_id=interaction_id
        ) as meter:
            try:
                result = await self._stt.transcribe(audio.data, audio.content_type)
            except SttUnavailableError as exc:
                meter.record(tokens_in=exc.tokens_in, tokens_out=exc.tokens_out)
                raise
            meter.record(tokens_in=result.tokens_in, tokens_out=result.tokens_out)
            return result.text

    async def _metered_twilio[T](
        self,
        call: Callable[[], Awaitable[T]],
        business_id: uuid.UUID | None,
        interaction_id: uuid.UUID | None,
    ) -> T:
        async with self._usage.metered(
            UsageKind.TELEPHONY_API, business_id=business_id, interaction_id=interaction_id
        ):
            return await call()

    def _delete_in_background(
        self, sid: str, business_id: uuid.UUID | None, interaction_id: uuid.UUID | None
    ) -> asyncio.Task[bool]:
        task = asyncio.create_task(self._delete(sid, business_id, interaction_id))
        _DELETING.add(task)
        task.add_done_callback(_DELETING.discard)
        return task

    async def _delete(
        self, sid: str, business_id: uuid.UUID | None, interaction_id: uuid.UUID | None
    ) -> bool:
        """One recording deleted at Twilio (already gone counts), under its own deadline.
        A failure is a logged reason code, never an exception."""
        recordings = self._recordings
        if recordings is None:
            logger.warning(
                "recording delete failed",
                extra={"business_id": str(business_id), "reason": "not_configured"},
            )
            return False
        try:
            async with asyncio.timeout(self._delete_timeout):
                await retry_bounded(
                    lambda: self._metered_twilio(
                        lambda: recordings.delete(sid), business_id, interaction_id
                    ),
                    RECORDING_RETRY,
                    twilio_retryable,
                )
            return True
        except Exception as exc:
            logger.warning(
                "recording delete failed",
                extra={
                    "business_id": str(business_id),
                    "interaction_id": str(interaction_id) if interaction_id else None,
                    "reason": _reason(exc),
                },
            )
            return False

    @staticmethod
    def _log(
        outcome: str,
        business_id: uuid.UUID,
        interaction_id: uuid.UUID | None,
        fetch_ms: int,
        stt_ms: int,
        started: float,
    ) -> None:
        logger.info(
            "stt",
            extra={
                "business_id": str(business_id),
                "interaction_id": str(interaction_id) if interaction_id else None,
                "outcome": outcome,
                "fetch_ms": fetch_ms,
                "stt_ms": stt_ms,
                "total_ms": _ms_since(started) if started else 0,
            },
        )


def _ms_since(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _reason(exc: Exception) -> str:
    """A fixed code for a log line: Twilio's own reason code, else the exception type."""
    if isinstance(exc, TwilioApiError):
        return exc.reason
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, UsageUnavailableError):
        return "usage_unavailable"
    return type(exc).__name__
