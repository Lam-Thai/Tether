"""`CallSessionService` at its boundary, on in-memory ports (no DB, no Twilio, no model).
The happy path, limits, holds and takeovers run end to end in `tests/api/test_voice.py`;
these pin the races and failure paths that are awkward to stage over HTTP."""

import asyncio
import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import pytest

from core.tenant import Tenant
from integrations.tts.fake import FakeTtsClient
from integrations.tts.port import TtsUnavailableError
from models.business import Business
from models.interaction import Interaction
from models.phone_number import PhoneNumber
from models.user import User
from services.call_session import (
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
from services.twiml import Close, Listen, TwimlRenderer, VoiceUrls
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
