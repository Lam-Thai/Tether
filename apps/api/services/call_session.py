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
- Voice (slice 15): with a `SpeechSynthesizer`, every step of an admitted call gets the
  agent's Gemini voice (`CallTarget.voice`, resolved by the voice catalog; `standard` is
  Twilio's <Say>) inside what is left of `response_budget_seconds`, Twilio's webhook
  timeout with a margin. Any TTS failure keeps `<Say>` for that line in the same turn.
  Only fixed lines are marked `cacheable` (greeting, holds, reprompts, fixed endings);
  a model reply never is. Refusals (unknown number, no plan, inactive agent, limits)
  are never synthesized: they touch no business data and spend nothing.
  Latency: the worker logs `llm_ms` (the whole engine turn), each webhook logs
  `tts_ms` and `latency_ms` (`tether.voice`); with `twilio_stt`, speech-to-text happens
  at Twilio before the input webhook arrives, so it is not observable here.
- Gemini audio input (slice 15b, `VOICE_INPUT_MODE=gemini_audio`): with a configured
  `SpeechRecognizer` the caller is recorded instead and `respond` gets a `Recording`. It
  is transcribed in the webhook, before the turn is claimed (so the stored utterance is
  text, as before), inside what is left of the response budget, and deleted at Twilio on
  every path: a replayed or already-claimed turn, a call past its time limit and a call
  that already ended (the caller hung up mid-recording) delete it untranscribed, and
  `complete` sweeps whatever a failed delete left. A failed transcription (over quota,
  provider or Twilio failure) never guesses: the caller hears `NOT_HEARD_LINE` in the
  same turn and the rest of the call uses Twilio's recognizer (`stt` rides in the signed
  URLs, like `silences`). An empty transcript is silence. Every voice turn log line
  carries `input_mode`, plus `fetch_ms` and `stt_ms` when a recording was transcribed.

No FastAPI or SQLAlchemy imports: everything comes in through ports.
"""

import asyncio
import contextlib
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol

from core.tenant import Tenant
from models.business import Business
from models.interaction import Interaction
from models.phone_number import PhoneNumber
from services.conversation import TurnResult, quota_fallback
from services.redaction import redact
from services.speech import SpeechSynthesizer
from services.speech_input import Recording, SpeechRecognizer, Transcript
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
# The recording couldn't be transcribed: ask again (now through Twilio's recognizer).
NOT_HEARD_LINE = "Sorry, I didn't quite catch that. Could you say it again?"

INPUT_TWILIO_STT = "twilio_stt"
INPUT_GEMINI_AUDIO = "gemini_audio"


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
    # Everything one webhook may spend before answering (Twilio gives up at ~15 s); speech
    # synthesis gets only what is left of it.
    response_budget_seconds: float = 12.5


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
    # The agent's stored voice (`None` = the configured default, `standard` = <Say>).
    voice: str | None = None

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


@dataclass
class _Input:
    """How one webhook heard the caller: `stt` once the call uses Twilio's recognizer
    (always in twilio_stt mode), and the transcript's timings when one was made."""

    stt: bool
    transcript: Transcript | None = None
    handed_over: bool = False  # the recording went to `transcribe`, which deletes it


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
        speech: SpeechSynthesizer | None = None,
        recognizer: SpeechRecognizer | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._claims = claims
        self._usage = usage
        self._run_turn = run_turn
        self._replay = replay
        self._end = end
        self._settings = settings
        self._speech = speech
        # Present whenever Twilio is configured, so a recording is always deleted (even
        # with the mode switched off mid-call). Recording mode only when it can also
        # transcribe; `core/voice` gives the renderer its `RecordSettings` on the same
        # condition, so the two always agree.
        self._recognizer = recognizer
        self._records = recognizer is not None and recognizer.configured
        self._clock = clock

    # --- webhooks ----------------------------------------------------------------------

    async def start(self, call: InboundCall) -> CallStep:
        started = time.monotonic()
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
            return await self._rejoin(existing, target, started)

        try:
            everyone, mine = await self._store.lock_admission_and_count(
                business_id, self._usage.interaction_idle_minutes
            )
            # Re-checked under the admission lock: a duplicate webhook racing this one has
            # either committed its interaction (rejoin it) or waits behind us.
            existing = await self._store.find_call(call.call_sid)
            if existing is not None:
                await self._store.checkpoint()
                return await self._rejoin(existing, target, started)
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
                    return await self._rejoin(existing, target, started)
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
        return await self._voiced(
            Listen(target.greeting, 1, cacheable=True), target, interaction.id, started
        )

    async def respond(
        self,
        call_sid: str,
        turn: int,
        silences: int,
        speech: str | None,
        *,
        recording: Recording | None = None,
        stt: bool = False,
    ) -> CallStep:
        """One caller utterance: Twilio's transcript (`speech`), or a `recording` to
        transcribe (gemini_audio). `stt`: this call already fell back to Twilio's
        recognizer."""
        started = time.monotonic()
        heard = _Input(stt=stt or not self._records)
        call: _Call | Close | None = None
        try:
            call = await self._active_call(call_sid)
            if not isinstance(call, _Call):
                return call
            step = await self._respond(
                call,
                turn,
                silences,
                (speech or "").strip(),
                recording if self._records else None,
                heard,
            )
        finally:
            # Retention (PRD §13.4): a recording not handed to `transcribe` (which deletes
            # it itself) is deleted here on every other path: replayed or claimed turns,
            # the time limit, an ended or unknown call, recording mode switched off
            # mid-call, and any exception above.
            if recording is not None and not heard.handed_over:
                await self._discard(recording, call if isinstance(call, _Call) else None)
        assert isinstance(call, _Call)
        return await self._voiced_and_logged(call, turn, started, step, "respond", heard)

    async def resume(
        self, call_sid: str, turn: int, attempt: int, *, stt: bool = False
    ) -> CallStep:
        started = time.monotonic()
        call = await self._active_call(call_sid)
        if not isinstance(call, _Call):
            return call
        await self._store.checkpoint()
        # The wait leaves room to synthesize the reply inside Twilio's timeout.
        step = await self._await_turn(call, turn, attempt, self._wait_budget())
        heard = _Input(stt=stt or not self._records)
        return await self._voiced_and_logged(call, turn, started, step, "resume", heard)

    async def complete(self, call_sid: str, status: str, duration_seconds: int | None) -> None:
        """A call status callback. Only a terminal status ends the interaction."""
        if status not in TERMINAL_STATUSES or not CALL_SID.match(call_sid):
            return
        interaction = await self._store.find_call(call_sid)
        if interaction is None:
            return  # acknowledged and ignored: not a call we started
        business_id, interaction_id = interaction.business_id, interaction.id
        try:
            target = await self._store.call_target(business_id)
            if target is None:
                return
            closed = await self._end(target.tenant, interaction_id)
            # Once per call: only the callback that closed it records the seconds. If the
            # idle sweep closed it first they go unrecorded (undercount, never double).
            if closed and duration_seconds:
                await self._usage.record(
                    UsageKind.CALL_SECONDS,
                    duration_seconds,
                    business_id=business_id,
                    interaction_id=interaction_id,
                )
            logger.info(
                "call ended",
                extra={
                    "interaction_id": str(interaction_id),
                    "call_status": status,
                    "closed": closed,
                },
            )
        finally:
            if self._records and self._recognizer is not None:
                # After the close (bounded, never raises), even if closing failed: whatever
                # a failed delete left at Twilio goes (PRD §13.4).
                with contextlib.suppress(Exception):
                    await self._store.checkpoint()
                await self._recognizer.sweep(
                    call_sid, business_id=business_id, interaction_id=interaction_id
                )

    # --- turns ---------------------------------------------------------------------------

    async def _respond(
        self,
        call: _Call,
        turn: int,
        silences: int,
        speech: str,
        recording: Recording | None,
        heard: _Input,
    ) -> CallStep:
        interaction, target = call.interaction, call.target
        hold_after = self._settings.hold_after_seconds
        if self._over_time(interaction):
            return Close(time_limit_line(target.business), cacheable=True)  # `respond` deletes
        if recording is not None:
            listening = time.monotonic()
            if await self._turn_taken(call, turn):
                # A retried webhook: the turn is answered or being answered; `respond`
                # deletes its recording untranscribed.
                return await self._await_turn(call, turn, 0, hold_after)
            # Never hold the request's transaction (opened by the reads above) across Twilio
            # and Gemini. Committed BEFORE the hand-over: if this raises, `respond` still
            # deletes the recording.
            await self._store.checkpoint()
            heard.handed_over = True  # from here on `transcribe` owns the delete
            transcript = await self._transcribe(call, recording)
            if transcript is None:
                if await self._turn_taken(call, turn):  # a duplicate claimed it meanwhile
                    return await self._await_turn(call, turn, 0, hold_after)
                # Never a guessed utterance: ask again, through Twilio's recognizer now.
                heard.stt = True
                return Listen(NOT_HEARD_LINE, turn, silences, cacheable=True)
            heard.transcript = transcript
            speech = transcript.text
            # The caller still hears something about `hold_after` after they stop talking.
            hold_after = max(hold_after - (time.monotonic() - listening), self._min_wait())
        if not speech:
            if silences + 1 >= self._settings.max_silences:
                return Close(NO_INPUT_LINE, cacheable=True)
            return Listen(REPROMPT_LINE, turn, silences + 1, cacheable=True)

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
            return await self._await_turn(call, turn, 0, hold_after)
        return await self._run_and_wait(call, view.id, claim, utterance, turn, 0, hold_after)

    async def _turn_taken(self, call: _Call, turn: int) -> bool:
        """Already answered, or claimed by another delivery of the same webhook."""
        interaction = call.interaction
        if await self._replay(call.target.tenant, interaction.id, turn_key(turn)) is not None:
            return True
        return await self._claims.get(interaction.business_id, interaction.id, turn) is not None

    async def _transcribe(self, call: _Call, recording: Recording) -> Transcript | None:
        """Deletes the recording on every path, so it must run with nothing left that can
        raise before it (the caller has already committed). The deadline leaves the turn
        its first wait inside Twilio's timeout."""
        assert self._recognizer is not None
        return await self._recognizer.transcribe(
            recording,
            business_id=call.interaction.business_id,
            interaction_id=call.interaction.id,
            deadline_seconds=self._settings.response_budget_seconds
            - self._settings.hold_after_seconds,
        )

    async def _discard(self, recording: Recording, call: _Call | None) -> None:
        """Deletes a recording this webhook won't transcribe. Never raises: it runs in a
        `finally`, possibly after a database error. Without an active call (ended,
        unknown) the Twilio calls are metered globally."""
        if self._recognizer is None:
            return  # no Twilio credentials: nothing could have recorded
        # Don't hold the request's transaction across Twilio; a failed session can't
        # commit, and the delete doesn't need it.
        with contextlib.suppress(Exception):
            await self._store.checkpoint()
        await self._recognizer.discard(
            recording,
            business_id=call.interaction.business_id if call else None,
            interaction_id=call.interaction.id if call else None,
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
                return Close(give_up_line(target.business), cacheable=True)
            if view.status == "failed":
                return Listen(TROUBLE_LINE, turn + 1, cacheable=True)
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
            return Listen(TROUBLE_LINE, turn + 1, cacheable=True)
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
        started = time.monotonic()
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
        # The whole engine turn: model calls, tools (calendar, booking) and the DB writes.
        logger.info(
            "voice turn worker",
            extra={
                "interaction_id": str(interaction_id),
                "turn_key": key,
                "llm_ms": int((time.monotonic() - started) * 1000),
            },
        )
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

    async def _rejoin(
        self, interaction: Interaction, target: CallTarget, started: float
    ) -> CallStep:
        """A repeated voice webhook for a call already started: no second interaction."""
        if interaction.status != "in_progress":
            return Close(GOODBYE)
        interaction_id = interaction.id
        turn = await self._store.next_turn(interaction.business_id, interaction_id)
        return await self._voiced(
            Listen(target.greeting, turn, cacheable=True), target, interaction_id, started
        )

    def _hold(self, business: Business, turn: int, attempt: int) -> CallStep:
        if attempt > self._settings.max_holds:
            return Close(give_up_line(business), cacheable=True)
        line = HOLDING_LINE if attempt == 1 else STILL_HOLDING_LINE
        return Hold(line, turn, attempt, cacheable=True)

    def _wait_budget(self) -> float:
        """One `resume` request's polling budget: the configured wait, minus room to
        synthesize the reply inside Twilio's timeout when the voice is on (never less
        than two polls)."""
        if self._speech is None or not self._speech.configured:
            return self._settings.wait_seconds
        spare = self._settings.response_budget_seconds - self._speech.timeout_seconds
        return max(min(self._settings.wait_seconds, spare), 2 * self._settings.poll_seconds)

    async def _voiced(
        self, step: CallStep, target: CallTarget, interaction_id: uuid.UUID, started: float
    ) -> CallStep:
        """`step` with the agent's synthesized voice attached, when there is one and time
        left in this webhook; otherwise `step` as is (<Say>)."""
        if self._speech is None:
            return step
        voice = self._speech.catalog.tts_voice(target.voice)
        if voice is None or not self._speech.configured:
            return step
        business_id = target.business.id
        # Never hold the request's transaction (opened by the reads above) across TTS.
        await self._store.checkpoint()
        remaining = self._settings.response_budget_seconds - (time.monotonic() - started)
        ref = await self._speech.synthesize(
            step.text,
            voice,
            business_id=business_id,
            interaction_id=interaction_id,
            cacheable=step.cacheable,
            deadline_seconds=remaining,
        )
        return replace(step, audio=ref.token) if ref is not None else step

    async def _voiced_and_logged(
        self, call: _Call, turn: int, started: float, step: CallStep, webhook: str, heard: _Input
    ) -> CallStep:
        if heard.stt and self._records and isinstance(step, Listen | Hold):
            # The fallback sticks: every later URL of this call asks for Twilio's STT.
            step = replace(step, stt=True)
        decided = time.monotonic()
        step = await self._voiced(step, call.target, call.interaction.id, started)
        tts_ms = int((time.monotonic() - decided) * 1000)
        self._log_latency(call, turn, started, step, webhook, tts_ms, heard)
        return step

    def _min_wait(self) -> float:
        return 2 * self._settings.poll_seconds

    def _over_time(self, interaction: Interaction) -> bool:
        elapsed = (self._clock() - interaction.started_at).total_seconds()
        return elapsed >= self._settings.max_call_seconds

    @staticmethod
    def _log_latency(
        call: _Call,
        turn: int,
        started: float,
        step: CallStep,
        webhook: str,
        tts_ms: int,
        heard: _Input,
    ) -> None:
        latency_ms = int((time.monotonic() - started) * 1000)
        extra: dict[str, object] = {
            "interaction_id": str(call.interaction.id),
            "turn": turn,
            "webhook": webhook,
            "step": type(step).__name__.lower(),
            "audio": "play" if step.audio else "say",
            "input_mode": INPUT_TWILIO_STT if heard.stt else INPUT_GEMINI_AUDIO,
            "tts_ms": tts_ms,
            "latency_ms": latency_ms,
        }
        if heard.transcript is not None:
            extra["fetch_ms"] = heard.transcript.fetch_ms
            extra["stt_ms"] = heard.transcript.stt_ms
        if latency_ms > LATENCY_TARGET_MS:
            logger.warning("voice turn slow", extra=extra)
        else:
            logger.info("voice turn", extra=extra)


def _forget(task: asyncio.Task[TurnResult]) -> None:
    _RUNNING.discard(task)
    if not task.cancelled():
        task.exception()  # retrieved (already logged in `_work`), so asyncio doesn't warn
