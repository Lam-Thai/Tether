"""`SpeechRecognizer` at its boundary: `FakeRecordingsApi`, `FakeSpeechToText` and an
in-memory usage store. Every failure is a `None` (the call reprompts and switches to
Twilio's speech recognition), and the recording is deleted at Twilio on EVERY path."""

import asyncio
import logging
import uuid

import pytest

from integrations.stt.fake import FakeSpeechToText
from integrations.stt.port import SttUnavailableError
from integrations.twilio.fake import FakeRecordingsApi
from integrations.twilio.port import (
    REASON_AUTH_FAILED,
    REASON_PROVIDER_ERROR,
    REASON_TRANSPORT,
    TwilioApiError,
)
from services import speech_input
from services.speech_input import Recording, SpeechRecognizer, Transcript
from services.usage import DailyLimit, UsageKind
from tests.services.memory_usage_store import MemoryUsageStore, guard, limits

CALL = f"CA{'a' * 32}"
# One second of 8 kHz 16-bit audio after a 44-byte header: long enough to be speech.
SPOKEN_WAV = b"RIFF" + b"\x00" * 40 + b"\x01\x02" * 8000


def recognizer(
    stt: FakeSpeechToText | None,
    recordings: FakeRecordingsApi | None,
    usage: MemoryUsageStore,
    *,
    timeout: float = 5.0,
    **daily: DailyLimit,
) -> SpeechRecognizer:
    return SpeechRecognizer(
        stt,
        recordings,
        guard(usage, limits(**daily) if daily else None),
        timeout_seconds=timeout,
        delete_timeout_seconds=1.0,
    )


@pytest.fixture
def parts():
    return FakeSpeechToText(), FakeRecordingsApi(), MemoryUsageStore()


def _recorded(recordings: FakeRecordingsApi, data: bytes = SPOKEN_WAV) -> Recording:
    return Recording(sid=recordings.add(CALL, data), call_sid=CALL)


async def test_an_utterance_is_fetched_transcribed_redacted_and_deleted(parts):
    stt, recordings, usage = parts
    business, interaction = uuid.uuid4(), uuid.uuid4()
    recording = _recorded(recordings)
    stt.say("  Book me in,   my card is 4242 4242 4242 4242  ")

    result = await recognizer(stt, recordings, usage).transcribe(
        recording, business_id=business, interaction_id=interaction
    )

    assert isinstance(result, Transcript)
    assert "4242" not in result.text and result.text.startswith("Book me in, my card is")
    assert result.fetch_ms >= 0 and result.stt_ms >= 0
    assert stt.requests == [(len(SPOKEN_WAV), "audio/x-wav")]
    assert recordings.deleted == [recording.sid] and recordings.recordings == {}
    assert usage.count(UsageKind.STT_REQUEST, business) == 1
    assert usage.count(UsageKind.GEMINI_TOKENS, business) == 110  # audio is billed in tokens
    assert usage.count(UsageKind.TELEPHONY_API, business) == 2  # fetch + delete


async def test_a_reached_limit_makes_no_gemini_call_and_still_deletes(parts):
    stt, recordings, usage = parts
    business = uuid.uuid4()
    recording = _recorded(recordings)
    limited = recognizer(
        stt, recordings, usage, stt_request=DailyLimit(per_day=10, per_business_per_day=1)
    )
    usage.counts[(business, UsageKind.STT_REQUEST.value, guard().window().day)] = 1

    assert await limited.transcribe(recording, business_id=business) is None
    assert stt.requests == []
    assert recordings.deleted == [recording.sid]


async def test_a_usage_store_outage_fails_closed_and_still_deletes(parts):
    stt, recordings, usage = parts
    recording = _recorded(recordings)
    usage.fail_reserve = True  # STT is capped: closed. Twilio calls are metered-only: open.

    assert (
        await recognizer(stt, recordings, usage).transcribe(recording, business_id=uuid.uuid4())
        is None
    )
    assert stt.requests == []
    assert recordings.deleted == [recording.sid]


async def test_a_failed_fetch_makes_no_gemini_call_and_still_deletes(parts):
    stt, recordings, usage = parts
    recording = _recorded(recordings)
    recordings.fail_next("fetch", TwilioApiError(REASON_AUTH_FAILED))

    assert (
        await recognizer(stt, recordings, usage).transcribe(recording, business_id=uuid.uuid4())
        is None
    )
    assert stt.requests == []
    assert [op for op, _ in recordings.calls] == ["fetch", "delete"]
    assert recordings.deleted == [recording.sid]


async def test_a_retryable_fetch_failure_is_tried_once_more(parts):
    stt, recordings, usage = parts
    business = uuid.uuid4()
    recording = _recorded(recordings)
    recordings.fail_next("fetch", TwilioApiError(REASON_TRANSPORT, retryable=True))

    result = await recognizer(stt, recordings, usage).transcribe(recording, business_id=business)

    assert result is not None
    assert [op for op, _ in recordings.calls] == ["fetch", "fetch", "delete"]
    assert usage.count(UsageKind.TELEPHONY_API, business) == 3


async def test_every_gemini_attempt_is_metered_and_a_retryable_failure_is_retried(parts):
    stt, recordings, usage = parts
    business = uuid.uuid4()
    stt.fail_next(SttUnavailableError("timeout", retryable=True))

    result = await recognizer(stt, recordings, usage).transcribe(
        _recorded(recordings), business_id=business
    )

    assert result is not None and result.text == "What are your opening hours?"
    assert len(stt.requests) == 2
    assert usage.count(UsageKind.STT_REQUEST, business) == 2
    assert usage.count(UsageKind.FAILED_REQUEST, business) == 1


async def test_a_provider_failure_is_none_after_the_bounded_retry(parts):
    stt, recordings, usage = parts
    recording = _recorded(recordings)
    stt.fail_next(SttUnavailableError("server", retryable=True), times=5)

    assert (
        await recognizer(stt, recordings, usage).transcribe(recording, business_id=uuid.uuid4())
        is None
    )
    assert len(stt.requests) == 2  # STT_RETRY: never more
    assert recordings.deleted == [recording.sid]


async def test_a_non_retryable_failure_is_not_retried(parts):
    stt, recordings, usage = parts
    stt.fail_next(SttUnavailableError("blocked"))

    assert (
        await recognizer(stt, recordings, usage).transcribe(
            _recorded(recordings), business_id=uuid.uuid4()
        )
        is None
    )
    assert len(stt.requests) == 1


async def test_the_deadline_bounds_the_whole_transcription_and_the_delete_still_runs(parts):
    _, recordings, usage = parts
    slow = FakeSpeechToText(delay_seconds=1.0)
    recording = _recorded(recordings)

    result = await recognizer(slow, recordings, usage, timeout=5.0).transcribe(
        recording, business_id=uuid.uuid4(), deadline_seconds=0.1
    )

    assert result is None
    assert recordings.deleted == [recording.sid]


async def test_no_time_left_is_none_without_any_provider_call_but_deletes(parts):
    stt, recordings, usage = parts
    recording = _recorded(recordings)

    result = await recognizer(stt, recordings, usage).transcribe(
        recording, business_id=uuid.uuid4(), deadline_seconds=0
    )

    assert result is None and stt.requests == []
    assert [op for op, _ in recordings.calls] == ["delete"]


async def test_a_silent_recording_is_an_empty_transcript_with_no_gemini_call(parts):
    stt, recordings, usage = parts
    recording = _recorded(recordings, data=b"RIFF" + b"\x00" * 40 + b"\x00\x00" * 100)

    result = await recognizer(stt, recordings, usage).transcribe(
        recording, business_id=uuid.uuid4()
    )

    assert result is not None and result.text == ""
    assert stt.requests == []
    assert recordings.deleted == [recording.sid]


async def test_no_speech_in_the_audio_is_an_empty_transcript(parts):
    stt, recordings, usage = parts
    stt.say("   ")

    result = await recognizer(stt, recordings, usage).transcribe(
        _recorded(recordings), business_id=uuid.uuid4()
    )

    assert result is not None and result.text == ""


async def test_a_failed_delete_is_logged_as_a_code_and_the_transcript_still_returns(parts, caplog):
    stt, recordings, usage = parts
    recording = _recorded(recordings)
    recordings.fail_next("delete", TwilioApiError(REASON_PROVIDER_ERROR, retryable=True), times=2)

    with caplog.at_level(logging.INFO, logger="tether.speech_input"):
        result = await recognizer(stt, recordings, usage).transcribe(
            recording, business_id=uuid.uuid4()
        )

    assert result is not None
    assert recording.sid in recordings.recordings  # left for the end-of-call sweep
    failed = [r for r in caplog.records if r.getMessage() == "recording delete failed"]
    assert [r.__dict__["reason"] for r in failed] == [REASON_PROVIDER_ERROR]
    assert all("What are your" not in r.getMessage() for r in caplog.records)


async def test_a_malformed_recording_sid_touches_nothing(parts):
    stt, recordings, usage = parts

    result = await recognizer(stt, recordings, usage).transcribe(
        Recording(sid="https://evil.example/x", call_sid=CALL), business_id=uuid.uuid4()
    )

    assert result is None and recordings.calls == [] and stt.requests == []


async def test_without_speech_to_text_the_recording_is_still_deleted(parts):
    _, recordings, usage = parts
    recording = _recorded(recordings)
    unconfigured = recognizer(None, recordings, usage)

    assert not unconfigured.configured
    assert await unconfigured.transcribe(recording, business_id=uuid.uuid4()) is None
    assert recordings.deleted == [recording.sid]


async def test_a_cancelled_transcription_still_deletes_the_recording(parts):
    _, recordings, usage = parts
    slow = FakeSpeechToText(delay_seconds=5.0)
    recording = _recorded(recordings)
    task = asyncio.create_task(
        recognizer(slow, recordings, usage).transcribe(recording, business_id=uuid.uuid4())
    )
    while not slow.requests:
        await asyncio.sleep(0.01)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(50):
        if recordings.deleted:
            break
        await asyncio.sleep(0.01)

    assert recordings.deleted == [recording.sid]


async def test_discard_deletes_without_transcribing_and_is_idempotent(parts):
    stt, recordings, usage = parts
    recognize = recognizer(stt, recordings, usage)
    recording = _recorded(recordings)

    assert await recognize.discard(recording, business_id=uuid.uuid4())
    assert await recognize.discard(recording, business_id=uuid.uuid4())  # already gone
    assert stt.requests == [] and recordings.deleted == [recording.sid]


async def test_the_sweep_deletes_every_recording_left_for_that_call_only(parts):
    stt, recordings, usage = parts
    left = [recordings.add(CALL), recordings.add(CALL)]
    other = recordings.add(f"CA{'b' * 32}")

    deleted = await recognizer(stt, recordings, usage).sweep(CALL, business_id=uuid.uuid4())

    assert deleted == 2 and sorted(recordings.deleted) == sorted(left)
    assert list(recordings.recordings) == [other]


async def test_a_slow_sweep_is_cut_off_and_never_raises(parts, monkeypatch):
    stt, _, usage = parts
    slow = FakeRecordingsApi(delay_seconds=1.0)
    slow.add(CALL)
    monkeypatch.setattr(speech_input, "SWEEP_TIMEOUT_SECONDS", 0.1)

    assert await recognizer(stt, slow, usage).sweep(CALL, business_id=uuid.uuid4()) == 0


async def test_discard_without_a_business_meters_globally(parts):
    stt, recordings, usage = parts
    recording = _recorded(recordings)

    assert await recognizer(stt, recordings, usage).discard(recording, business_id=None)
    assert recordings.deleted == [recording.sid]
    assert usage.count(UsageKind.TELEPHONY_API) == 1  # the global counter only


async def test_a_failed_sweep_never_raises(parts):
    stt, recordings, usage = parts
    recordings.add(CALL)
    recordings.fail_next("list_for_call", TwilioApiError(REASON_AUTH_FAILED))

    assert await recognizer(stt, recordings, usage).sweep(CALL, business_id=uuid.uuid4()) == 0
