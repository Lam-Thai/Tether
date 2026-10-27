"""`CallSessionService`: a Twilio phone call, turn by turn, on the shared engine.

Four calls, one per webhook (`start` = voice, `respond` = input, `resume` = wait,
`complete` = status), each returning the next `CallStep` (`services/twiml.py`):

- The business comes from the NUMBER called (`phone_numbers`, unique), never from the
  payload. An unknown number, a business without a plan, or an inactive agent gets a
  polite fixed line and a hang-up, and touches no business data.
- `CallSid` maps to ONE interaction, idempotently: the voice-only unique index on
  `interactions.external_call_id` plus a re-check under the admission lock, so Twilio
  retries and duplicate webhooks never start a second interaction or count a second
  call. A new call must pass concurrency admission and reserve one `call` in
  `UsageGuard` (calls/day); a refusal is a fixed line + hang-up.
- Each caller utterance is turn N (N rides in the Gather URL Twilio signs). The engine
  runs it with turn key `voice:N`, so a replayed webhook returns the stored reply and
  appends nothing (checked again under the interaction lock, so racing workers store it
  once). The same `ConversationEngine` as the test chat: this module only adapts it.
- Latency: Twilio waits ~15 s for a webhook. `respond` waits `hold_after_seconds` for
  the turn; past that the caller hears a short holding phrase and Twilio is redirected
  to `resume`, which returns the stored reply once it exists. The turn worker runs
  shielded on its OWN database session and heartbeats its `voice_turns` claim; if the
  worker died (a serverless function frozen after answering), `resume` sees the stale
  heartbeat, takes the claim over (a conditional UPDATE) and runs the turn itself.
  After `max_holds` holds the caller gets a fixed apology with the business's contacts
  and the call ends. Every turn's latency is logged (`tether.voice`), a warning over 5 s.
- Silence reprompts (`silences` also rides in the signed URL), then hangs up after
  `max_silences`. Past `max_call_seconds` the call ends with a fixed line. A reached
  Gemini limit mid-call (the engine's quota fallback, which gives the business's own
  contacts) hangs up cleanly (`twiml.step_for`).
- `complete` (a terminal call status) ends the interaction through the shared
  `InteractionLifecycle` (outcome, summary) and records the call's seconds, once.

No FastAPI or SQLAlchemy imports: everything comes in through ports.
"""

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from core.tenant import Tenant
from models.business import Business
from models.interaction import Interaction
from models.phone_number import PhoneNumber
from services.conversation import TurnResult, quota_fallback
from services.redaction import redact
from services.twiml import GOODBYE, CallStep, Close, Hold, Listen, step_for
from services.usage import (
    ActiveCounts,
    ConcurrencyLimitedError,
    LimitExceededError,
    UsageGuard,
    UsageKind,
    UsageUnavailableError,
)

logger = logging.getLogger("tether.voice")

CHANNEL = "voice"
# Twilio CallSid shape; anything else is not a call we could have started.
CALL_SID = re.compile(r"^CA[0-9a-f]{32}$")
E164 = re.compile(r"^\+[1-9][0-9]{6,14}$")
TERMINAL_STATUSES = frozenset({"completed", "busy", "no-answer", "failed", "canceled"})
# A caller utterance longer than this is cut (Twilio transcripts are far shorter).
MAX_UTTERANCE_CHARS = 1000
LATENCY_TARGET_MS = 5000

# The fixed caller-facing lines: the only voice text that doesn't come from the model, the
# owner's greeting, or the conversation engine's own fallbacks.
UNKNOWN_NUMBER_LINE = "Sorry, this number isn't set up to take calls right now. Goodbye."
BUSY_LINE = "Sorry, all our lines are busy right now. Please try again in a few minutes. Goodbye."
REPROMPT_LINE = "Sorry, I didn't catch that. How can I help?"
NO_INPUT_LINE = "I didn't hear anything, so I'll end the call now. Goodbye."
HOLDING_LINE = "One moment, please."
STILL_HOLDING_LINE = "Thanks for waiting, I'm still working on that."
TROUBLE_LINE = "Sorry, I had trouble with that. Could you say it again?"


def turn_key(turn: int) -> str:
    return f"voice:{turn}"


def _contacts(business: Business) -> str:
    contacts = [c for c in (business.phone, business.contact_email) if c]
    if contacts:
        return f"You can reach the team at {' or '.join(contacts)}."
    return f"Please contact {business.name} directly."


def unavailable_line(business: Business) -> str:
    """No plan, or the owner switched the agent off: nothing is promised."""
    return (
        f"Thanks for calling {business.name}. We can't take calls on this line right now. Goodbye."
    )


def time_limit_line(business: Business) -> str:
    return f"We've reached the time limit for this call. {_contacts(business)} {GOODBYE}"


def give_up_line(business: Business) -> str:
    """The turn didn't finish in time. It may still finish (a booking would then exist and
    show in the inbox), so this claims nothing either way."""
    return f"Sorry, this is taking longer than expected. {_contacts(business)} {GOODBYE}"


@dataclass(frozen=True)
class VoiceSettings:
    max_call_seconds: int
    # How long `respond` waits for the turn before a holding phrase (the caller then hears
    # something within ~5 s of finishing speaking).
    hold_after_seconds: float = 4.0
    # One `resume` request's whole budget, under Twilio's ~15 s webhook timeout.
    wait_seconds: float = 12.0
    max_holds: int = 3
    max_silences: int = 2
    heartbeat_seconds: float = 1.0
    # A claim whose heartbeat is older than this is presumed dead and may be taken over.
    # Several missed beats (each opens a connection: NullPool), so a slow database doesn't
    # start a second worker on a live turn; well under `wait_seconds`, so a dead worker's
    # turn is still taken over inside one wait request.
    stale_after_seconds: float = 5.0
    poll_seconds: float = 0.5


@dataclass(frozen=True)
class InboundCall:
    call_sid: str
    to: str
    caller: str | None


@dataclass(frozen=True)
class CallTarget:
    """The business a number or call resolved to, read fresh per webhook."""

    tenant: Tenant
    greeting: str
    agent_active: bool
    entitled: bool

    @property
    def business(self) -> Business:
        assert self.tenant.business is not None
        return self.tenant.business


@dataclass(frozen=True)
class TurnClaim:
    id: uuid.UUID
    turn: int
    utterance: str | None
    status: str  # pending | answered | failed
    claim: uuid.UUID
    created: bool  # this `claim` call inserted it


class CallStore(Protocol):
    """Bound to the webhook request's session."""

    async def find_number(self, phone_number: str) -> PhoneNumber | None:
        """The associated number a call came in on (the row decides the business)."""
        ...

    async def find_call(self, call_sid: str) -> Interaction | None:
        """The voice interaction for this CallSid, re-read from the database."""
        ...

    async def call_target(self, business_id: uuid.UUID) -> CallTarget | None: ...
    async def lock_admission_and_count(
        self, business_id: uuid.UUID, idle_minutes: int
    ) -> tuple[int, int]:
        """Raises `ConcurrencyLimitedError` when the admission lock can't be taken."""
        ...

    async def create_call(
        self,
        *,
        business_id: uuid.UUID,
        phone_number_id: uuid.UUID,
        call_sid: str,
        caller: str | None,
        greeting: str,
    ) -> Interaction:
        """The call's interaction with the greeting as agent message 1 (idempotent)."""
        ...

    async def next_turn(self, business_id: uuid.UUID, interaction_id: uuid.UUID) -> int: ...
    async def checkpoint(self) -> None: ...
    async def rollback(self) -> None: ...


class TurnClaims(Protocol):
    """`voice_turns`, each call its own committed transaction (shared across workers)."""

    async def claim(
        self,
        business_id: uuid.UUID,
        interaction_id: uuid.UUID,
        turn: int,
        utterance: str,
        claim: uuid.UUID,
    ) -> TurnClaim:
        """Inserts the pending turn, or returns the existing one (`created=False`)."""
        ...

    async def get(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID, turn: int
    ) -> TurnClaim | None: ...
    async def heartbeat(self, turn_id: uuid.UUID, claim: uuid.UUID) -> bool:
        """False when the claim is no longer ours (taken over or finished)."""
        ...

    async def take_over(
        self, turn_id: uuid.UUID, seen: uuid.UUID, new: uuid.UUID, stale_after_seconds: float
    ) -> bool:
        """Claims a pending turn whose heartbeat is stale (database time); False otherwise."""
        ...

    async def finish(self, turn_id: uuid.UUID, claim: uuid.UUID, status: str) -> None:
        """`answered` or `failed`; clears the stored utterance."""
        ...


# (business_id, interaction_id, utterance, turn_key) -> the engine's turn, run and
# committed on a session of its own (it may outlive the webhook request).
TurnRunner = Callable[[uuid.UUID, uuid.UUID, str, str], Awaitable[TurnResult]]
# The stored turn for a key, fresh from the database, or None (no model call).
Replayer = Callable[[Tenant, uuid.UUID, str], Awaitable[TurnResult | None]]
# Ends the interaction (lifecycle); True when this call closed it.
Ender = Callable[[Tenant, uuid.UUID], Awaitable[bool]]

# Handles to running turn workers, only so the event loop doesn't garbage-collect them
# mid-turn. Never conversation state: everything a turn needs or produces is in Postgres.
_RUNNING: set[asyncio.Task[TurnResult]] = set()


@dataclass(frozen=True)
class _Call:
    interaction: Interaction
    target: CallTarget


class CallSessionService:
    def __init__(
        self,
        store: CallStore,
        claims: TurnClaims,
        usage: UsageGuard,
        *,
        run_turn: TurnRunner,
        replay: Replayer,
        end: Ender,
        settings: VoiceSettings,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._claims = claims
        self._usage = usage
        self._run_turn = run_turn
        self._replay = replay
        self._end = end
        self._settings = settings
        self._clock = clock

    # --- webhooks ----------------------------------------------------------------------

    async def start(self, call: InboundCall) -> CallStep:
        if not CALL_SID.match(call.call_sid):
            return Close(UNKNOWN_NUMBER_LINE)
        number = await self._store.find_number(call.to)
        if number is None:
            logger.info("call to an unassociated number")
            return Close(UNKNOWN_NUMBER_LINE)
        business_id, number_id = number.business_id, number.id
        target = await self._store.call_target(business_id)
        if target is None:
            return Close(UNKNOWN_NUMBER_LINE)
        if not target.entitled or not target.agent_active:
            logger.info(
                "call refused: agent unavailable",
                extra={"business_id": str(business_id), "entitled": target.entitled},
            )
            return Close(unavailable_line(target.business))
        existing = await self._store.find_call(call.call_sid)
        if existing is not None:
            return await self._rejoin(existing, target)

        try:
            everyone, mine = await self._store.lock_admission_and_count(
                business_id, self._usage.interaction_idle_minutes
            )
            # Re-checked under the admission lock: a duplicate webhook racing this one has
            # either committed its interaction (rejoin it) or waits behind us.
            existing = await self._store.find_call(call.call_sid)
            if existing is not None:
                await self._store.checkpoint()
                return await self._rejoin(existing, target)
            self._usage.check_admission(ActiveCounts(all_businesses=everyone, business=mine))
            async with self._usage.metered(UsageKind.CALL, business_id=business_id):
                pass  # an inbound call has no provider call to wrap: this reserves one call
        except (ConcurrencyLimitedError, LimitExceededError, UsageUnavailableError) as exc:
            # The line is built BEFORE the rollback, which expires the business row.
            if isinstance(exc, ConcurrencyLimitedError):
                line = BUSY_LINE
            elif isinstance(exc, LimitExceededError):
                line = f"{quota_fallback(target.business)} {GOODBYE}"
            else:
                line = unavailable_line(target.business)
            await self._store.rollback()
            if isinstance(exc, ConcurrencyLimitedError):
                # A duplicate webhook that waited out the admission lock behind the request
                # creating this very call: rejoin it rather than hang up on the caller.
                existing = await self._store.find_call(call.call_sid)
                if existing is not None:
                    return await self._rejoin(existing, target)
            logger.info(
                "call refused",
                extra={"business_id": str(business_id), "reason": type(exc).__name__},
            )
            return Close(line)

        caller = call.caller if call.caller and E164.match(call.caller) else None
        interaction = await self._store.create_call(
            business_id=business_id,
            phone_number_id=number_id,
            call_sid=call.call_sid,
            caller=caller,
            greeting=target.greeting,
        )
        await self._store.checkpoint()
        logger.info(
            "call started",
            extra={"business_id": str(business_id), "interaction_id": str(interaction.id)},
        )
        return Listen(target.greeting, 1)

    async def respond(
        self, call_sid: str, turn: int, silences: int, speech: str | None
    ) -> CallStep:
        started = time.monotonic()
        call = await self._active_call(call_sid)
        if not isinstance(call, _Call):
            return call
        step = await self._respond(call, turn, silences, (speech or "").strip())
        self._log_latency(call, turn, started, step, "respond")
        return step

    async def resume(self, call_sid: str, turn: int, attempt: int) -> CallStep:
        started = time.monotonic()
        call = await self._active_call(call_sid)
        if not isinstance(call, _Call):
            return call
        await self._store.checkpoint()
        step = await self._await_turn(call, turn, attempt, self._settings.wait_seconds)
        self._log_latency(call, turn, started, step, "resume")
        return step

    async def complete(self, call_sid: str, status: str, duration_seconds: int | None) -> None:
        """A call status callback. Only a terminal status ends the interaction."""
        if status not in TERMINAL_STATUSES or not CALL_SID.match(call_sid):
            return
        interaction = await self._store.find_call(call_sid)
        if interaction is None:
            return  # acknowledged and ignored: not a call we started
        business_id, interaction_id = interaction.business_id, interaction.id
        target = await self._store.call_target(business_id)
        if target is None:
            return
        closed = await self._end(target.tenant, interaction_id)
        # Once per call: only the callback that closed it records the seconds. If the idle
        # sweep closed it first they go unrecorded (an undercount, never a double count).
        if closed and duration_seconds:
            await self._usage.record(
                UsageKind.CALL_SECONDS,
                duration_seconds,
                business_id=business_id,
                interaction_id=interaction_id,
            )
        logger.info(
            "call ended",
            extra={"interaction_id": str(interaction_id), "call_status": status, "closed": closed},
        )

    # --- turns ---------------------------------------------------------------------------

    async def _respond(self, call: _Call, turn: int, silences: int, speech: str) -> CallStep:
        interaction, target = call.interaction, call.target
        if self._over_time(interaction):
            return Close(time_limit_line(target.business))
        if not speech:
            if silences + 1 >= self._settings.max_silences:
                return Close(NO_INPUT_LINE)
            return Listen(REPROMPT_LINE, turn, silences + 1)

        replayed = await self._replay(target.tenant, interaction.id, turn_key(turn))
        if replayed is not None:
            return step_for(replayed, next_turn=turn + 1)

        utterance = redact(speech)[:MAX_UTTERANCE_CHARS]
        await self._store.checkpoint()
        claim = uuid.uuid4()
        view = await self._claims.claim(
            interaction.business_id, interaction.id, turn, utterance, claim
        )
        if not view.created:
            # A retried webhook: the first delivery's worker owns this turn. Wait for it.
            return await self._await_turn(call, turn, 0, self._settings.hold_after_seconds)
        return await self._run_and_wait(
            call, view.id, claim, utterance, turn, 0, self._settings.hold_after_seconds
        )

    async def _await_turn(self, call: _Call, turn: int, attempt: int, budget: float) -> CallStep:
        """Returns the turn's reply once a worker stored it, taking a dead worker's claim
        over; past `budget`, one more hold (or the give-up line after `max_holds`)."""
        interaction, target = call.interaction, call.target
        loop = asyncio.get_running_loop()
        deadline = loop.time() + budget
        while True:
            replayed = await self._replay(target.tenant, interaction.id, turn_key(turn))
            if replayed is not None:
                return step_for(replayed, next_turn=turn + 1)
            view = await self._claims.get(interaction.business_id, interaction.id, turn)
            if view is None:
                return Close(give_up_line(target.business))
            if view.status == "failed":
                return Listen(TROUBLE_LINE, turn + 1)
            if view.status == "pending" and view.utterance:
                new = uuid.uuid4()
                if await self._claims.take_over(
                    view.id, view.claim, new, self._settings.stale_after_seconds
                ):
                    logger.warning(
                        "voice turn taken over",
                        extra={"interaction_id": str(interaction.id), "turn": turn},
                    )
                    # The old worker may have stored it just before going quiet.
                    replayed = await self._replay(target.tenant, interaction.id, turn_key(turn))
                    if replayed is not None:
                        await self._finish_quietly(view.id, new, "answered")
                        return step_for(replayed, next_turn=turn + 1)
                    remaining = max(deadline - loop.time(), 0.0)
                    return await self._run_and_wait(
                        call, view.id, new, view.utterance, turn, attempt, remaining
                    )
            if loop.time() >= deadline:
                return self._hold(target.business, turn, attempt + 1)
            await self._store.checkpoint()  # never sit idle in a transaction while polling
            await asyncio.sleep(self._settings.poll_seconds)

    async def _run_and_wait(
        self,
        call: _Call,
        turn_id: uuid.UUID,
        claim: uuid.UUID,
        utterance: str,
        turn: int,
        attempt: int,
        budget: float,
    ) -> CallStep:
        interaction = call.interaction
        task = asyncio.create_task(
            self._work(
                interaction.business_id, interaction.id, turn_id, claim, utterance, turn_key(turn)
            )
        )
        _RUNNING.add(task)
        task.add_done_callback(_forget)
        try:
            # Shielded: a hold returns to Twilio while the worker keeps going.
            result = await asyncio.wait_for(asyncio.shield(task), budget)
        except TimeoutError:
            return self._hold(call.target.business, turn, attempt + 1)
        except Exception:
            return Listen(TROUBLE_LINE, turn + 1)
        return step_for(result, next_turn=turn + 1)

    async def _work(
        self,
        business_id: uuid.UUID,
        interaction_id: uuid.UUID,
        turn_id: uuid.UUID,
        claim: uuid.UUID,
        utterance: str,
        key: str,
    ) -> TurnResult:
        beat = asyncio.create_task(self._heartbeat(turn_id, claim))
        try:
            result = await self._run_turn(business_id, interaction_id, utterance, key)
        except BaseException as exc:
            logger.warning(
                "voice turn failed",
                extra={"interaction_id": str(interaction_id), "error_type": type(exc).__name__},
            )
            await self._finish_quietly(turn_id, claim, "failed")
            raise
        finally:
            beat.cancel()
        await self._finish_quietly(turn_id, claim, "answered")
        return result

    async def _heartbeat(self, turn_id: uuid.UUID, claim: uuid.UUID) -> None:
        while True:
            await asyncio.sleep(self._settings.heartbeat_seconds)
            try:
                if not await self._claims.heartbeat(turn_id, claim):
                    return  # taken over: the engine's turn key still stores it only once
            except Exception as exc:
                logger.warning("voice heartbeat failed", extra={"error_type": type(exc).__name__})

    async def _finish_quietly(self, turn_id: uuid.UUID, claim: uuid.UUID, status: str) -> None:
        try:
            await self._claims.finish(turn_id, claim, status)
        except Exception as exc:
            # Only the mirror of the engine's turn key: `resume` still finds the reply.
            logger.warning("voice turn finish failed", extra={"error_type": type(exc).__name__})

    # --- helpers -------------------------------------------------------------------------

    async def _active_call(self, call_sid: str) -> _Call | Close:
        if not CALL_SID.match(call_sid):
            return Close(UNKNOWN_NUMBER_LINE)
        interaction = await self._store.find_call(call_sid)
        if interaction is None:
            return Close(UNKNOWN_NUMBER_LINE)
        target = await self._store.call_target(interaction.business_id)
        if target is None:
            return Close(UNKNOWN_NUMBER_LINE)
        if interaction.status != "in_progress":
            return Close(GOODBYE)
        return _Call(interaction, target)

    async def _rejoin(self, interaction: Interaction, target: CallTarget) -> CallStep:
        """A repeated voice webhook for a call already started: no second interaction."""
        if interaction.status != "in_progress":
            return Close(GOODBYE)
        turn = await self._store.next_turn(interaction.business_id, interaction.id)
        return Listen(target.greeting, turn)

    def _hold(self, business: Business, turn: int, attempt: int) -> CallStep:
        if attempt > self._settings.max_holds:
            return Close(give_up_line(business))
        return Hold(HOLDING_LINE if attempt == 1 else STILL_HOLDING_LINE, turn, attempt)

    def _over_time(self, interaction: Interaction) -> bool:
        elapsed = (self._clock() - interaction.started_at).total_seconds()
        return elapsed >= self._settings.max_call_seconds

    @staticmethod
    def _log_latency(call: _Call, turn: int, started: float, step: CallStep, webhook: str) -> None:
        latency_ms = int((time.monotonic() - started) * 1000)
        extra = {
            "interaction_id": str(call.interaction.id),
            "turn": turn,
            "webhook": webhook,
            "step": type(step).__name__.lower(),
            "latency_ms": latency_ms,
        }
        if latency_ms > LATENCY_TARGET_MS:
            logger.warning("voice turn slow", extra=extra)
        else:
            logger.info("voice turn", extra=extra)


def _forget(task: asyncio.Task[TurnResult]) -> None:
    _RUNNING.discard(task)
    if not task.cancelled():
        task.exception()  # retrieved (already logged in `_work`), so asyncio doesn't warn
