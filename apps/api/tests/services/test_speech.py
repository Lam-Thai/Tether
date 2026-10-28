"""`SpeechSynthesizer` at its boundary: `FakeTtsClient`, an in-memory audio store and an
in-memory usage store. Every failure is a `None` (the call says the line with <Say>)."""

import uuid
import wave
from io import BytesIO

import pytest

from integrations.tts.fake import FakeTtsClient
from integrations.tts.port import TtsUnavailableError
from services.speech import (
    CALL_AUDIO_TYPE,
    STANDARD_VOICE,
    PreviewUnavailableError,
    SpeechNotConfiguredError,
    SpeechSynthesizer,
    SpeechUnavailableError,
    UnknownVoiceError,
    VoiceCatalog,
)
from services.usage import DailyLimit, LimitExceededError, UsageKind
from tests.services.memory_audio_store import MemoryAudioStore
from tests.services.memory_usage_store import MemoryUsageStore, guard, limits

MODEL = "gemini-tts-test"
REPLY_TTL = 300
CACHE_TTL = 86400
CATALOG = VoiceCatalog(["Kore", "Puck"], "Kore")


def speech(
    tts: FakeTtsClient | None,
    audio: MemoryAudioStore,
    usage: MemoryUsageStore,
    *,
    timeout: float = 5.0,
    cache_ttl: int = CACHE_TTL,
    **daily: DailyLimit,
) -> SpeechSynthesizer:
    return SpeechSynthesizer(
        tts,
        audio,
        guard(usage, limits(**daily) if daily else None),
        catalog=CATALOG,
        model=MODEL,
        reply_ttl_seconds=REPLY_TTL,
        cache_ttl_seconds=cache_ttl,
        timeout_seconds=timeout,
    )


@pytest.fixture
def parts():
    return FakeTtsClient(), MemoryAudioStore(), MemoryUsageStore()


async def test_a_reply_is_one_metered_request_stored_as_short_lived_mulaw_wav(parts):
    tts, audio, usage = parts
    business, interaction = uuid.uuid4(), uuid.uuid4()

    ref = await speech(tts, audio, usage).synthesize(
        "Your haircut is at 2 PM.", "Puck", business_id=business, interaction_id=interaction
    )

    assert ref is not None and not ref.cached
    assert tts.requests == [("Your haircut is at 2 PM.", "Puck")]
    assert usage.count(UsageKind.TTS_REQUEST, business) == 1
    stored = audio.objects[ref.token]
    assert stored.content_type == CALL_AUDIO_TYPE
    assert stored.ttl_seconds == REPLY_TTL  # a reply is never cached
    assert audio.cache == {}
    assert stored.data[:4] == b"RIFF" and stored.data[8:12] == b"WAVE"
    assert int.from_bytes(stored.data[20:22], "little") == 7  # WAVE_FORMAT_MULAW
    assert int.from_bytes(stored.data[24:28], "little") == 8000


async def test_a_fixed_line_is_synthesized_once_then_reused_with_a_fresh_token(parts):
    tts, audio, usage = parts
    business = uuid.uuid4()
    synth = speech(tts, audio, usage)

    first = await synth.synthesize(
        "One moment, please.", "Kore", business_id=business, cacheable=True
    )
    again = await synth.synthesize(
        "  One   moment, please. ", "Kore", business_id=business, cacheable=True
    )

    assert first is not None and again is not None
    assert not first.cached and again.cached
    assert first.token != again.token  # every URL is its own short-lived token
    assert len(tts.requests) == 1
    assert usage.count(UsageKind.TTS_REQUEST, business) == 1
    assert next(iter(audio.cache.values())).ttl_seconds == CACHE_TTL


async def test_the_cache_is_per_business_voice_and_text(parts):
    tts, audio, usage = parts
    synth = speech(tts, audio, usage)
    a, b = uuid.uuid4(), uuid.uuid4()

    await synth.synthesize("Hello.", "Kore", business_id=a, cacheable=True)
    await synth.synthesize("Hello.", "Kore", business_id=b, cacheable=True)  # other business
    await synth.synthesize("Hello.", "Puck", business_id=a, cacheable=True)  # other voice
    await synth.synthesize("Hello!", "Kore", business_id=a, cacheable=True)  # changed text

    assert len(tts.requests) == 4


async def test_a_zero_cache_ttl_turns_caching_off(parts):
    tts, audio, usage = parts
    synth = speech(tts, audio, usage, cache_ttl=0)
    business = uuid.uuid4()

    await synth.synthesize("Hello.", "Kore", business_id=business, cacheable=True)
    await synth.synthesize("Hello.", "Kore", business_id=business, cacheable=True)

    assert len(tts.requests) == 2 and audio.cache == {}


async def test_a_reached_limit_falls_back_with_no_provider_call_and_no_retry(parts):
    tts, audio, usage = parts
    business = uuid.uuid4()
    synth = speech(tts, audio, usage, tts_request=DailyLimit(per_day=10, per_business_per_day=0))

    assert await synth.synthesize("Hi.", "Kore", business_id=business) is None
    assert tts.requests == []
    assert audio.puts == 0


async def test_a_transient_failure_is_retried_once_and_both_attempts_count(parts):
    tts, audio, usage = parts
    business = uuid.uuid4()
    tts.fail_next(TtsUnavailableError("5xx", retryable=True))

    ref = await speech(tts, audio, usage).synthesize("Hi.", "Kore", business_id=business)

    assert ref is not None
    assert len(tts.requests) == 2
    assert usage.count(UsageKind.TTS_REQUEST, business) == 2
    assert usage.count(UsageKind.FAILED_REQUEST, business) == 1


async def test_a_permanent_failure_falls_back_after_one_attempt(parts):
    tts, audio, usage = parts
    tts.fail_next(TtsUnavailableError("bad request"))

    assert (
        await speech(tts, audio, usage).synthesize("Hi.", "Kore", business_id=uuid.uuid4()) is None
    )
    assert len(tts.requests) == 1


async def test_two_transient_failures_fall_back(parts):
    tts, audio, usage = parts
    tts.fail_next(TtsUnavailableError("429", retryable=True, rate_limited=True), times=2)

    assert (
        await speech(tts, audio, usage).synthesize("Hi.", "Kore", business_id=uuid.uuid4()) is None
    )
    assert len(tts.requests) == 2


async def test_a_slow_provider_falls_back_at_the_deadline(parts):
    tts, audio, usage = parts
    tts.delay_seconds = 1.0

    ref = await speech(tts, audio, usage, timeout=0.05).synthesize(
        "Hi.", "Kore", business_id=uuid.uuid4()
    )

    assert ref is None and audio.puts == 0


async def test_the_callers_deadline_caps_the_timeout_and_none_left_skips_tts(parts):
    tts, audio, usage = parts
    tts.delay_seconds = 1.0
    synth = speech(tts, audio, usage, timeout=5.0)

    assert (
        await synth.synthesize("Hi.", "Kore", business_id=uuid.uuid4(), deadline_seconds=0.05)
        is None
    )
    tts.delay_seconds = 0.0
    tts.requests.clear()
    assert (
        await synth.synthesize("Hi.", "Kore", business_id=uuid.uuid4(), deadline_seconds=0) is None
    )
    assert tts.requests == []


async def test_a_storage_failure_falls_back(parts):
    tts, audio, usage = parts
    audio.fail_puts = True

    assert (
        await speech(tts, audio, usage).synthesize("Hi.", "Kore", business_id=uuid.uuid4()) is None
    )


async def test_unconfigured_blank_or_overlong_text_makes_no_request(parts):
    tts, audio, usage = parts
    business = uuid.uuid4()

    assert await speech(None, audio, usage).synthesize("Hi.", "Kore", business_id=business) is None
    synth = speech(tts, audio, usage)
    assert await synth.synthesize("   ", "Kore", business_id=business) is None
    assert await synth.synthesize("x" * 1001, "Kore", business_id=business) is None
    assert tts.requests == []
    assert usage.count(UsageKind.TTS_REQUEST, business) == 0


# --- preview -------------------------------------------------------------------------


async def test_preview_is_a_browser_playable_wav_and_warms_the_call_cache(parts):
    tts, audio, usage = parts
    business = uuid.uuid4()
    synth = speech(tts, audio, usage)

    data = await synth.preview("Thanks for calling!", "Puck", business_id=business)

    with wave.open(BytesIO(data)) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 8000)
        assert wav.getnframes() > 0
    ref = await synth.synthesize(
        "Thanks for calling!", "Puck", business_id=business, cacheable=True
    )
    assert ref is not None and ref.cached
    assert len(tts.requests) == 1


async def test_preview_errors(parts):
    tts, audio, usage = parts
    business = uuid.uuid4()
    synth = speech(tts, audio, usage)

    with pytest.raises(UnknownVoiceError):
        await synth.preview("Hi.", "Nobody", business_id=business)
    with pytest.raises(PreviewUnavailableError):
        await synth.preview("Hi.", STANDARD_VOICE, business_id=business)
    with pytest.raises(SpeechNotConfiguredError):
        await speech(None, audio, usage).preview("Hi.", "Kore", business_id=business)
    tts.fail_next(TtsUnavailableError("400"))
    with pytest.raises(SpeechUnavailableError):
        await synth.preview("Hi.", "Kore", business_id=business)
    limited = speech(tts, audio, usage, tts_request=DailyLimit(per_day=10, per_business_per_day=0))
    tts.requests.clear()
    with pytest.raises(LimitExceededError):
        await limited.preview("Hello there.", "Kore", business_id=business)
    assert tts.requests == []


# --- catalog -------------------------------------------------------------------------


def test_catalog_resolves_stored_voices():
    assert CATALOG.effective(None) == "Kore"
    assert CATALOG.effective("Puck") == "Puck"
    assert CATALOG.effective("Removed") == "Kore"  # dropped from settings later
    assert CATALOG.effective(STANDARD_VOICE) == STANDARD_VOICE
    assert CATALOG.tts_voice(None) == "Kore"
    assert CATALOG.tts_voice(STANDARD_VOICE) is None
    assert [v.id for v in CATALOG.options()] == ["Kore", "Puck", STANDARD_VOICE]
    assert CATALOG.is_valid("Puck") and not CATALOG.is_valid("puck")
    with pytest.raises(ValueError):
        VoiceCatalog(["Kore"], "Puck")


async def test_a_preview_storage_failure_is_a_503_not_a_500(parts):
    tts, audio, usage = parts
    audio.fail_puts = True

    with pytest.raises(SpeechUnavailableError):
        await speech(tts, audio, usage).preview("Hi.", "Kore", business_id=uuid.uuid4())
