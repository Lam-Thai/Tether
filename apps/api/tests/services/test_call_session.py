"""`CallSessionService` at its boundary, on in-memory ports (no DB, no Twilio, no model).
The happy path, limits, holds and takeovers run end to end in `tests/api/test_voice.py`;
these pin the races and failure paths that are awkward to stage over HTTP."""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import pytest

from core.tenant import Tenant
from integrations.stt.fake import FakeSpeechToText
from integrations.stt.port import SttUnavailableError
from integrations.tts.fake import FakeTtsClient
from integrations.tts.port import TtsUnavailableError
from integrations.twilio.fake import FakeRecordingsApi
from models.business import Business
from models.interaction import Interaction
from models.phone_number import PhoneNumber
from models.user import User
from services.call_session import (
    NOT_HEARD_LINE,
    REPROMPT_LINE,
    TROUBLE_LINE,
    UNKNOWN_NUMBER_LINE,
    CallSessionService,
    CallTarget,
    InboundCall,
    TurnClaim,
    VoiceSettings,
    turn_key,
)
from services.conversation import MessageView, TurnResult
from services.speech import SpeechSynthesizer, VoiceCatalog
from services.speech_input import Recording, SpeechRecognizer
from services.twiml import Close, Hold, Listen, TwimlRenderer, VoiceUrls
from services.usage import ConcurrencyLimitedError, DailyLimit, UsageKind
from tests.services.memory_audio_store import MemoryAudioStore
from tests.services.memory_usage_store import MemoryUsageStore, guard, limits

NUMBER = "+15550001111"
CALLER = "+15557654321"


def _tenant() -> Tenant:
    user = User(id=uuid.uuid4(), external_auth_id="user_x", email="owner@example.com")
    business = Business(
        id=uuid.uuid4(),
        owner_id=user.id,
        name="Alpha Salon",
        industry="Salon",
        address="1 Main St",
        timezone="America/New_York",
        phone="+15550002222",
        contact_email=None,
    )
    return Tenant(user=user, business=business)


class MemoryCallStore:
    def __init__(self, tenant: Tenant) -> None:
        assert tenant.business is not None
        self.target = CallTarget(tenant, "Hi, Alpha Salon here.", True, True)
        self.number = PhoneNumber(id=uuid.uuid4(), business_id=tenant.business.id)
        self.calls: dict[str, Interaction] = {}

    async def find_number(self, phone_number: str) -> PhoneNumber | None:
        return self.number if phone_number == NUMBER else None

    async def find_call(self, call_sid: str) -> Interaction | None:
        return self.calls.get(call_sid)

    async def call_target(self, business_id: uuid.UUID) -> CallTarget | None:
        return self.target

    async def lock_admission_and_count(self, business_id, idle_minutes) -> tuple[int, int]:
        return 0, 0

    async def create_call(self, *, business_id, phone_number_id, call_sid, caller, greeting):
        interaction = Interaction(
            id=uuid.uuid4(),
            business_id=business_id,
            channel="voice",
            external_call_id=call_sid,
            caller_identifier=caller,
            status="in_progress",
            state={},
            started_at=datetime.now(UTC),
        )
        self.calls.setdefault(call_sid, interaction)
        return self.calls[call_sid]

    async def next_turn(self, business_id, interaction_id) -> int:
        return 1

    async def checkpoint(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


@dataclass
class _Row:
    view: TurnClaim
    heartbeat: float


class MemoryClaims:
    def __init__(self) -> None:
        self.rows: dict[tuple[uuid.UUID, int], _Row] = {}

    async def claim(self, business_id, interaction_id, turn, utterance, claim) -> TurnClaim:
        key = (interaction_id, turn)
        if key in self.rows:
            return self.rows[key].view
        view = TurnClaim(uuid.uuid4(), turn, utterance, "pending", claim, created=True)
        self.rows[key] = _Row(replace(view, created=False), time.monotonic())
        return view

    async def get(self, business_id, interaction_id, turn) -> TurnClaim | None:
        row = self.rows.get((interaction_id, turn))
        return row.view if row else None

    def _by_id(self, turn_id: uuid.UUID) -> _Row:
        return next(row for row in self.rows.values() if row.view.id == turn_id)

    async def heartbeat(self, turn_id, claim) -> bool:
        row = self._by_id(turn_id)
        if row.view.claim != claim or row.view.status != "pending":
            return False
        row.heartbeat = time.monotonic()
        return True

    async def take_over(self, turn_id, seen, new, stale_after_seconds) -> bool:
        row = self._by_id(turn_id)
        stale = time.monotonic() - row.heartbeat > stale_after_seconds
        if row.view.claim != seen or row.view.status != "pending" or not stale:
            return False
        row.view, row.heartbeat = replace(row.view, claim=new), time.monotonic()
        return True

    async def finish(self, turn_id, claim, status) -> None:
        row = self._by_id(turn_id)
        if status == "answered" or (row.view.claim == claim and row.view.status == "pending"):
            row.view = replace(row.view, status=status, utterance=None)


class Turns:
    """The engine stand-in: `run` stores a reply under the turn key; `replay` reads it."""

    def __init__(self) -> None:
        self.stored: dict[tuple[uuid.UUID, str], TurnResult] = {}
        self.runs = 0
        self.gate: asyncio.Event | None = None
        self.error: Exception | None = None

    async def run(self, business_id, interaction_id, utterance, key) -> TurnResult:
        self.runs += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        now = datetime.now(UTC)
        result = TurnResult(
            interaction_id=interaction_id,
            reply_text=f"Reply to: {utterance}",
            intent="UNKNOWN",
            status="in_progress",
            ended=False,
            fallback=False,
            usage_limited=False,
            messages=(
                MessageView(2, "customer", utterance, now),
                MessageView(3, "agent", f"Reply to: {utterance}", now),
            ),
        )
        self.stored.setdefault((interaction_id, key), result)
        return result

    async def replay(self, tenant, interaction_id, key) -> TurnResult | None:
        return self.stored.get((interaction_id, key))


@pytest.fixture
def parts():
    tenant = _tenant()
    store, claims, turns = MemoryCallStore(tenant), MemoryClaims(), Turns()
    usage_store = MemoryUsageStore()
    ended: list[uuid.UUID] = []

    async def end(tenant, interaction_id) -> bool:
        first = interaction_id not in ended
        ended.append(interaction_id)
        return first

    service = CallSessionService(
        store,
        claims,
        guard(usage_store),
        run_turn=turns.run,
        replay=turns.replay,
        end=end,
        settings=VoiceSettings(
            max_call_seconds=600,
            hold_after_seconds=0.5,
            wait_seconds=1.0,
            heartbeat_seconds=0.05,
            stale_after_seconds=0.3,
            poll_seconds=0.02,
        ),
    )
    return service, store, claims, turns, usage_store, ended


def _sid() -> str:
    return f"CA{uuid.uuid4().hex}"


async def test_a_worker_error_is_an_apology_and_the_turn_is_marked_failed(parts):
    service, store, claims, turns, *_ = parts
    sid = _sid()
    assert await service.start(InboundCall(sid, NUMBER, CALLER)) == Listen(
        "Hi, Alpha Salon here.", 1, cacheable=True
    )
    turns.error = RuntimeError("database went away")

    step = await service.respond(sid, 1, 0, "Do you do beards?")

    assert step == Listen(TROUBLE_LINE, 2, cacheable=True)
    [row] = claims.rows.values()
    assert (row.view.status, row.view.utterance) == ("failed", None)
    # A retry of that same webhook doesn't run the turn again: it reports the failure.
    assert await service.respond(sid, 1, 0, "Do you do beards?") == Listen(
        TROUBLE_LINE, 2, cacheable=True
    )
    assert turns.runs == 1


async def test_a_duplicate_input_waits_for_the_first_worker_instead_of_running_again(parts):
    service, store, claims, turns, *_ = parts
    sid = _sid()
    await service.start(InboundCall(sid, NUMBER, CALLER))
    turns.gate = asyncio.Event()

    first = asyncio.create_task(service.respond(sid, 1, 0, "Any openings Friday?"))
    await asyncio.sleep(0.05)
    second = asyncio.create_task(service.respond(sid, 1, 0, "Any openings Friday?"))
    await asyncio.sleep(0.05)
    turns.gate.set()

    expected = Listen("Reply to: Any openings Friday?", 2)
    assert await first == expected and await second == expected
    assert turns.runs == 1


async def test_the_caller_id_is_kept_only_when_it_is_a_phone_number(parts):
    service, store, *_ = parts
    for caller, kept in ((CALLER, CALLER), ("anonymous", None), ("client:alice", None)):
        sid = _sid()
        await service.start(InboundCall(sid, NUMBER, caller))
        assert store.calls[sid].caller_identifier == kept


async def test_unknown_numbers_and_malformed_sids_touch_nothing(parts):
    service, store, claims, turns, usage_store, ended = parts
    assert await service.start(InboundCall(_sid(), "+15559999999", CALLER)) == Close(
        UNKNOWN_NUMBER_LINE
    )
    assert await service.start(InboundCall("not-a-sid", NUMBER, CALLER)) == Close(
        UNKNOWN_NUMBER_LINE
    )
    assert await service.respond("CA" + "0" * 32, 1, 0, "hello") == Close(UNKNOWN_NUMBER_LINE)
    assert store.calls == {} and turns.runs == 0 and claims.rows == {}
    assert not usage_store.events


async def test_only_a_terminal_status_ends_the_call_and_seconds_count_once(parts):
    service, store, claims, turns, usage_store, ended = parts
    sid = _sid()
    await service.start(InboundCall(sid, NUMBER, CALLER))
    interaction_id = store.calls[sid].id

    await service.complete(sid, "in-progress", None)
    assert ended == []
    await service.complete(sid, "completed", 42)
    await service.complete(sid, "completed", 42)  # Twilio retried the callback

    assert ended == [interaction_id, interaction_id]
    seconds = [e for e in usage_store.events if e.kind is UsageKind.CALL_SECONDS]
    assert [e.units for e in seconds] == [42]
    assert turn_key(3) == "voice:3"


async def test_a_duplicate_voice_webhook_stuck_behind_the_admission_lock_rejoins_the_call(parts):
    service, store, *_ = parts
    sid = _sid()
    await service.start(InboundCall(sid, NUMBER, CALLER))
    first = store.calls.pop(sid)  # the duplicate reads "no call yet" before the lock...

    async def lock_timed_out(business_id, idle_minutes):
        # ...waits out the lock behind the first request, which commits the call meanwhile.
        store.calls[sid] = first
        raise ConcurrencyLimitedError("Too many calls are starting at once.")

    store.lock_admission_and_count = lock_timed_out  # type: ignore[method-assign]

    step = await service.start(InboundCall(sid, NUMBER, CALLER))

    assert step == Listen("Hi, Alpha Salon here.", 1, cacheable=True)  # rejoined, not the busy line
    assert store.calls == {sid: first}


async def test_a_takeover_returns_a_reply_the_quiet_worker_already_stored(parts):
    service, store, claims, turns, *_ = parts
    sid = _sid()
    await service.start(InboundCall(sid, NUMBER, CALLER))
    interaction = store.calls[sid]
    # A worker stored turn 1 and then went quiet before marking its claim answered.
    await claims.claim(interaction.business_id, interaction.id, 1, "Hi", uuid.uuid4())
    [row] = claims.rows.values()
    row.heartbeat -= 60
    stored = await turns.run(interaction.business_id, interaction.id, "Hi", turn_key(1))
    turns.runs = 0

    turns.stored.pop((interaction.id, turn_key(1)))
    original_take_over = claims.take_over

    async def take_over_then_stored(*args, **kwargs):
        won = await original_take_over(*args, **kwargs)
        turns.stored[(interaction.id, turn_key(1))] = stored  # landed just before
        return won

    claims.take_over = take_over_then_stored  # type: ignore[method-assign]

    step = await service.resume(sid, 1, 1)

    assert step == Listen("Reply to: Hi", 2)
    assert turns.runs == 0
    assert row.view.status == "answered"


# --- the agent's voice (slice 15) ------------------------------------------------------


def _voiced(parts, *, tts_limit: int = 100, budget: float = 13.5):
    service, store, claims, turns, usage_store, ended = parts
    tts, audio = FakeTtsClient(), MemoryAudioStore()
    usage = guard(
        usage_store, limits(tts_request=DailyLimit(per_day=100, per_business_per_day=tts_limit))
    )
    speech = SpeechSynthesizer(
        tts,
        audio,
        usage,
        catalog=VoiceCatalog(["Kore", "Puck"], "Kore"),
        model="tts-test",
        reply_ttl_seconds=300,
        cache_ttl_seconds=86400,
        timeout_seconds=5.0,
    )
    voiced = CallSessionService(
        store,
        claims,
        usage,
        run_turn=turns.run,
        replay=turns.replay,
        end=lambda tenant, interaction_id: asyncio.sleep(0, True),
        settings=VoiceSettings(
            max_call_seconds=600,
            hold_after_seconds=0.5,
            wait_seconds=1.0,
            heartbeat_seconds=0.05,
            stale_after_seconds=0.3,
            poll_seconds=0.02,
            response_budget_seconds=budget,
        ),
        speech=speech,
    )
    return voiced, store, tts, audio


async def test_the_greeting_is_voiced_once_and_reused_by_the_next_call(parts):
    service, store, tts, audio = _voiced(parts)

    first = await service.start(InboundCall(_sid(), NUMBER, CALLER))
    second = await service.start(InboundCall(_sid(), NUMBER, CALLER))

    assert isinstance(first, Listen) and isinstance(second, Listen)
    assert first.audio and second.audio and first.audio != second.audio
    assert tts.requests == [("Hi, Alpha Salon here.", "Kore")]  # the default voice
    assert len(audio.cache) == 1


async def test_a_reply_is_voiced_in_the_agents_voice_and_never_cached(parts):
    service, store, tts, audio = _voiced(parts)
    store.target = replace(store.target, voice="Puck")
    sid = _sid()
    await service.start(InboundCall(sid, NUMBER, CALLER))

    step = await service.respond(sid, 1, 0, "Any openings Friday?")

    assert isinstance(step, Listen) and step.text == "Reply to: Any openings Friday?"
    assert step.audio is not None and not step.cacheable
    assert tts.requests[-1] == ("Reply to: Any openings Friday?", "Puck")
    assert audio.objects[step.audio].ttl_seconds == 300
    assert len(audio.cache) == 1  # only the greeting


async def test_a_tts_failure_says_the_same_reply_in_the_same_turn(parts):
    service, store, tts, audio = _voiced(parts)
    sid = _sid()
    await service.start(InboundCall(sid, NUMBER, CALLER))
    tts.fail_next(TtsUnavailableError("bad request"))

    step = await service.respond(sid, 1, 0, "Do you do beards?")

    assert step == Listen("Reply to: Do you do beards?", 2)  # no audio: <Say>
    renderer = TwimlRenderer(VoiceUrls(input="/in", wait="/wait"))
    assert "<Say" in renderer.render(step) and "<Play" not in renderer.render(step)


async def test_over_the_tts_limit_the_call_continues_with_say_and_no_provider_call(parts):
    service, store, tts, audio = _voiced(parts, tts_limit=0)
    sid = _sid()

    greeting = await service.start(InboundCall(sid, NUMBER, CALLER))
    reply = await service.respond(sid, 1, 0, "Hello")

    assert greeting == Listen("Hi, Alpha Salon here.", 1, cacheable=True)
    assert reply == Listen("Reply to: Hello", 2)
    assert tts.requests == []


async def test_the_standard_voice_and_refusals_never_synthesize(parts):
    service, store, tts, audio = _voiced(parts)
    store.target = replace(store.target, voice="standard")

    greeting = await service.start(InboundCall(_sid(), NUMBER, CALLER))
    unknown = await service.start(InboundCall(_sid(), "+15559999999", CALLER))
    store.target = replace(store.target, voice=None, agent_active=False)
    inactive = await service.start(InboundCall(_sid(), NUMBER, CALLER))

    assert isinstance(greeting, Listen) and greeting.audio is None
    assert unknown == Close(UNKNOWN_NUMBER_LINE)
    assert isinstance(inactive, Close) and inactive.audio is None
    assert tts.requests == []


async def test_no_time_left_in_the_webhook_means_say(parts):
    service, store, tts, audio = _voiced(parts, budget=0.0)

    step = await service.start(InboundCall(_sid(), NUMBER, CALLER))

    assert isinstance(step, Listen) and step.audio is None
    assert tts.requests == []


# --- gemini_audio input (slice 15b) ----------------------------------------------------


def _recognizing(parts, *, stt_limit: int = 100, max_call_seconds: int = 600):
    _, store, claims, turns, usage_store, _ = parts
    stt, recordings = FakeSpeechToText(), FakeRecordingsApi()
    usage = guard(
        usage_store, limits(stt_request=DailyLimit(per_day=100, per_business_per_day=stt_limit))
    )
    recognizer = SpeechRecognizer(stt, recordings, usage, timeout_seconds=2.0)
    service = CallSessionService(
        store,
        claims,
        usage,
        run_turn=turns.run,
        replay=turns.replay,
        end=lambda tenant, interaction_id: asyncio.sleep(0, True),
        settings=VoiceSettings(
            max_call_seconds=max_call_seconds,
            hold_after_seconds=0.5,
            wait_seconds=1.0,
            heartbeat_seconds=0.05,
            stale_after_seconds=0.3,
            poll_seconds=0.02,
        ),
        recognizer=recognizer,
    )
    return service, store, turns, stt, recordings


def _record(recordings: FakeRecordingsApi, call_sid: str) -> Recording:
    """What a <Record> leaves at Twilio: half a second of (non-silent) audio."""
    spoken = b"RIFF" + b"\x00" * 40 + b"\x01\x02" * 4000
    return Recording(sid=recordings.add(call_sid, spoken), call_sid=call_sid)


async def test_a_recorded_utterance_reaches_the_engine_as_text_and_is_deleted(parts, caplog):
    service, store, turns, stt, recordings = _recognizing(parts)
    sid = _sid()
    await service.start(InboundCall(sid, NUMBER, CALLER))
    stt.say("Do you do beard trims?")
    recording = _record(recordings, sid)

    with caplog.at_level(logging.INFO, logger="tether.voice"):
        step = await service.respond(sid, 1, 0, None, recording=recording)

    assert step == Listen("Reply to: Do you do beard trims?", 2)
    assert turns.runs == 1 and recordings.recordings == {}
    [line] = [r for r in caplog.records if r.getMessage() == "voice turn"]
    assert line.__dict__["input_mode"] == "gemini_audio"
    assert line.__dict__["fetch_ms"] >= 0 and line.__dict__["stt_ms"] >= 0


async def test_a_failed_transcription_reprompts_and_switches_the_call_to_twilio_stt(parts, caplog):
    service, store, turns, stt, recordings = _recognizing(parts)
    sid = _sid()
    await service.start(InboundCall(sid, NUMBER, CALLER))
    stt.fail_next(SttUnavailableError("blocked"))

    with caplog.at_level(logging.INFO, logger="tether.voice"):
        step = await service.respond(sid, 1, 0, None, recording=_record(recordings, sid))

    # Logged as the gemini_audio attempt it was, never as a Twilio STT sample.
    [line] = [r for r in caplog.records if r.getMessage() == "voice turn"]
    assert line.__dict__["input_mode"] == "gemini_audio"
    assert line.__dict__["stt_fallback"] is True

    assert step == Listen(NOT_HEARD_LINE, 1, cacheable=True, stt=True)  # same turn, no guess
    assert turns.runs == 0 and recordings.recordings == {}
    # The next utterance comes from Twilio's recognizer; the flag sticks for the call.
    reply = await service.respond(sid, 1, 0, "Do you do beards?", stt=True)
    assert reply == Listen("Reply to: Do you do beards?", 2, stt=True)


async def test_over_the_stt_limit_the_call_falls_back_with_no_gemini_call(parts):
    service, store, turns, stt, recordings = _recognizing(parts, stt_limit=0)
    sid = _sid()
    await service.start(InboundCall(sid, NUMBER, CALLER))

    step = await service.respond(sid, 1, 0, None, recording=_record(recordings, sid))

    assert step == Listen(NOT_HEARD_LINE, 1, cacheable=True, stt=True)
    assert stt.requests == [] and recordings.recordings == {}


async def test_an_empty_transcript_is_silence_and_keeps_recording(parts):
    service, store, turns, stt, recordings = _recognizing(parts)
    sid = _sid()
    await service.start(InboundCall(sid, NUMBER, CALLER))
    stt.say("")

    step = await service.respond(sid, 1, 0, None, recording=_record(recordings, sid))

    assert step == Listen(REPROMPT_LINE, 1, 1, cacheable=True)
    assert turns.runs == 0 and recordings.recordings == {}


async def test_a_replayed_recorded_turn_is_not_transcribed_again_and_the_recording_goes(parts):
    service, store, turns, stt, recordings = _recognizing(parts)
    sid = _sid()
    await service.start(InboundCall(sid, NUMBER, CALLER))
    stt.say("Any openings Friday?")
    first = await service.respond(sid, 1, 0, None, recording=_record(recordings, sid))

    # Twilio retries the webhook: the recording it names is deleted, never transcribed.
    again = await service.respond(sid, 1, 0, None, recording=_record(recordings, sid))

    assert again == first and len(stt.requests) == 1
    assert recordings.recordings == {} and turns.runs == 1


async def test_past_the_time_limit_the_recording_is_deleted_untranscribed(parts):
    service, store, turns, stt, recordings = _recognizing(parts, max_call_seconds=30)
    sid = _sid()
    await service.start(InboundCall(sid, NUMBER, CALLER))
    store.calls[sid].started_at = datetime(2000, 1, 1, tzinfo=UTC)

    step = await service.respond(sid, 1, 0, None, recording=_record(recordings, sid))

    assert isinstance(step, Close) and stt.requests == []
    assert recordings.recordings == {}


async def test_a_hold_keeps_the_stt_flag_through_the_wait(parts):
    service, store, turns, stt, recordings = _recognizing(parts)
    sid = _sid()
    await service.start(InboundCall(sid, NUMBER, CALLER))
    turns.gate = asyncio.Event()

    held = await service.respond(sid, 1, 0, "Is Saturday open?", stt=True)
    turns.gate.set()
    resumed = await service.resume(sid, 1, 1, stt=True)

    assert isinstance(held, Hold) and held.stt
    assert resumed == Listen("Reply to: Is Saturday open?", 2, stt=True)


async def test_the_end_of_the_call_sweeps_recordings_left_at_twilio(parts):
    service, store, turns, stt, recordings = _recognizing(parts)
    sid = _sid()
    await service.start(InboundCall(sid, NUMBER, CALLER))
    leftover = recordings.add(sid)
    other_call = recordings.add(_sid())

    await service.complete(sid, "completed", 42)

    assert recordings.deleted == [leftover] and list(recordings.recordings) == [other_call]


async def test_a_recording_on_an_unknown_or_ended_call_is_still_deleted(parts):
    service, store, turns, stt, recordings = _recognizing(parts)
    unknown = _record(recordings, _sid())
    ended_sid = _sid()
    await service.start(InboundCall(ended_sid, NUMBER, CALLER))
    store.calls[ended_sid].status = "completed"  # the caller hung up mid-<Record>
    late = _record(recordings, ended_sid)

    assert await service.respond(unknown.call_sid, 1, 0, None, recording=unknown) == Close(
        UNKNOWN_NUMBER_LINE
    )
    assert isinstance(await service.respond(ended_sid, 1, 0, None, recording=late), Close)
    assert stt.requests == [] and recordings.recordings == {}


async def test_a_database_error_before_transcribing_still_deletes_the_recording(parts):
    service, store, turns, stt, recordings = _recognizing(parts)
    _, _, claims, *_ = parts
    sid = _sid()
    await service.start(InboundCall(sid, NUMBER, CALLER))

    async def broken_get(*args, **kwargs):
        raise RuntimeError("database went away")

    claims.get = broken_get
    with pytest.raises(RuntimeError):
        await service.respond(sid, 1, 0, None, recording=_record(recordings, sid))
    assert recordings.recordings == {} and stt.requests == []


async def test_a_failed_commit_before_transcribing_still_deletes_the_recording(parts):
    service, store, turns, stt, recordings = _recognizing(parts)
    sid = _sid()
    await service.start(InboundCall(sid, NUMBER, CALLER))
    commits = 0

    async def checkpoint_then_fail() -> None:
        # The commit before the hand-over to `transcribe` loses the connection; the
        # `finally` in `respond` must still delete (its own commit attempt then works).
        nonlocal commits
        commits += 1
        if commits == 1:
            raise ConnectionError("database connection dropped")

    store.checkpoint = checkpoint_then_fail
    with pytest.raises(ConnectionError):
        await service.respond(sid, 1, 0, None, recording=_record(recordings, sid))
    assert recordings.recordings == {} and stt.requests == []


async def test_with_recording_mode_off_mid_call_a_recording_is_deleted_and_treated_as_silence(
    parts,
):
    _, store, claims, turns, usage_store, _ = parts
    recordings = FakeRecordingsApi()
    # Twilio configured, mode `twilio_stt` (no speech-to-text): deletes, never records.
    off = SpeechRecognizer(None, recordings, guard(usage_store), timeout_seconds=2.0)
    service = CallSessionService(
        store,
        claims,
        guard(usage_store),
        run_turn=turns.run,
        replay=turns.replay,
        end=lambda tenant, interaction_id: asyncio.sleep(0, True),
        settings=VoiceSettings(max_call_seconds=600),
        recognizer=off,
    )
    sid = _sid()
    await service.start(InboundCall(sid, NUMBER, CALLER))

    step = await service.respond(sid, 1, 0, None, recording=_record(recordings, sid))

    assert step == Listen(REPROMPT_LINE, 1, 1, cacheable=True)  # no `stt` flag: not recording
    assert recordings.recordings == {}
    await service.complete(sid, "completed", 10)
    assert [op for op, _ in recordings.calls] == ["delete"]  # no sweep outside recording mode


async def test_the_sweep_runs_even_when_closing_the_interaction_fails(parts):
    _, store, claims, turns, usage_store, _ = parts
    stt, recordings = FakeSpeechToText(), FakeRecordingsApi()

    async def failing_end(tenant, interaction_id) -> bool:
        raise RuntimeError("summary store down")

    service = CallSessionService(
        store,
        claims,
        guard(usage_store),
        run_turn=turns.run,
        replay=turns.replay,
        end=failing_end,
        settings=VoiceSettings(max_call_seconds=600),
        recognizer=SpeechRecognizer(stt, recordings, guard(usage_store), timeout_seconds=2.0),
    )
    sid = _sid()
    await service.start(InboundCall(sid, NUMBER, CALLER))
    leftover = recordings.add(sid)

    with pytest.raises(RuntimeError):
        await service.complete(sid, "completed", 10)
    assert recordings.deleted == [leftover]


async def test_twilio_stt_mode_logs_its_input_mode_and_never_touches_recordings(parts, caplog):
    service, store, claims, turns, *_ = parts
    sid = _sid()
    await service.start(InboundCall(sid, NUMBER, CALLER))

    with caplog.at_level(logging.INFO, logger="tether.voice"):
        step = await service.respond(sid, 1, 0, "Hello")

    assert step == Listen("Reply to: Hello", 2)
    [line] = [r for r in caplog.records if r.getMessage() == "voice turn"]
    assert line.__dict__["input_mode"] == "twilio_stt"
    assert "fetch_ms" not in line.__dict__
