"""`ConversationEngine`: one customer turn in, one validated agent reply out.

Small surface (`handle_turn`, `end`, `transcript`) over the rules that keep a
conversation correct:

- Stateless between calls. Everything the next turn needs (messages, entities,
  failures) is read from the store; nothing is cached in the process.
- The owner's persona, and with a `BusinessDirectory` the business profile and
  opening hours, are reloaded on every turn, so an edit applies to the very
  next message with no restart. Hours go in the prompt (today's local date,
  the weekly hours, the next 7 days); services only through `get_services`.
- Gemini proposes, the backend validates. The raw reply is parsed against
  `AgentTurn`; a bad reply, or one asking for a tool that isn't registered,
  is invalid and is never acted on. One retry per turn, then a fixed
  fallback line.
- Tool loop (bounded). A valid reply with `tool_calls` is never shown: the
  calls go through `ToolExecutor` (validated, executed, written to the
  operation log), the store checkpoints so the log rows are durable and no
  transaction stays open, and the request is rebuilt with the same messages
  plus FAQ RESULTS / TOOL RESULTS in the system instruction. At most
  `MAX_TOOL_ROUNDS` rounds; a model that still asks for tools after that
  gets the no-info fallback. So one turn makes at most
  `MAX_TOOL_ROUNDS + 2` model calls (rounds, the final reply, one retry).
- Grounding guard (`services/grounding.py`). Every cited source must be
  valid in this turn (an FAQ or service a tool returned, `business_hours`
  when hours are listed, `business_profile`); a reply with a fact-stating
  intent (`GROUNDED_INTENTS`) must cite one of a kind that backs it; and every
  money amount and duration in the reply must match a service or FAQ
  retrieved in this turn. Otherwise the reply is replaced by the no-info
  fallback and the interaction is flagged for a human. Earlier turns never
  ground a later one: the owner may have edited or disabled the fact since.
- Time guard (same module). Every time of day in the reply must be a slot
  `check_availability` offered this turn (or, outside booking replies and when
  nothing was offered, an opening or closing time shown this turn). A miss gets
  one regeneration with a correction note (sharing the single retry with
  invalid output), then `availability_fallback` and a human flag. So the agent
  never names a time the calendar and the hours don't support.
- Tool side effects apply only to a reply that is shown: `ToolResult.state`
  (the offered slots) is merged into `interactions.state`, and a tool's
  `requires_human` flags the interaction. A fallback reply clears those keys
  instead, since the customer never heard the offer.
- Booking guard (same module). A reply may say an appointment is booked only when
  `create_booking` returned `confirmed` this turn, and may never say one was
  cancelled or moved (no tool does that yet). A miss gets one regeneration with
  `BOOKING_CORRECTION` (sharing the single retry), then `booking_fallback` and a
  human flag. So the customer is never told a booking exists that the backend
  didn't confirm.
- An action a tool really performed is never denied: once a tool returned a
  `confirmation` (a confirmed booking), any fallback this turn is replaced by that
  fixed, backend-written line, so the customer is never told "I can't confirm a
  booking" about one that exists.
- The fallback lines below (invalid output, provider, `quota_fallback`,
  `no_info_fallback`, `availability_fallback`, `booking_fallback`) are the only agent
  text that does not come from the model or from the owner's greeting. The last four
  point the customer to the business's own contact details and never promise a
  callback.
- Every model call goes through `UsageGuard.metered`, inside `retry_bounded`
  (so each attempt is metered). A reached limit means no model call at all:
  the turn answers with the quota fallback, which points the customer to the
  business's own contact details, and the interaction is flagged for a human.
  It is never retried. A new conversation is admitted only while the
  concurrency limits allow it.
- A row lock on the interaction is taken only after the model calls, just to
  allocate message sequence numbers and merge state. It is never held across
  a (slow) model call.
- One deadline per turn covers every model call, tool rounds included. It
  cancels model calls only: tool executors and checkpoints are DB work,
  bounded by the statement timeout, and never cut off mid-statement.
- Customer and model text are never logged at INFO or above.

No FastAPI or SQLAlchemy imports: the router passes a `ConversationStore`
(repositories bound to the request's session) and an `LLMClient`.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, time
from typing import Any, Literal, Protocol

from pydantic import ValidationError

from core.errors import AppError
from core.retry import RetryPolicy, retry_bounded
from core.tenant import Tenant
from integrations.llm.port import (
    LLM_RETRY,
    LLMClient,
    LLMUnavailableError,
    ModelMessage,
    ModelReply,
    ModelRequest,
    is_retryable,
)
from models.business import Business
from models.interaction import Interaction, InteractionMessage
from schemas.agent_turn import AgentTurn
from services.agent import AgentStore, load_agent, require_business
from services.availability_tool import earlier_offer
from services.directory import BusinessDirectory, Open
from services.escalation import Recorded, TurnEscalations
from services.escalation_policy import (
    EMERGENCY_REPLY,
    OperationFact,
    TurnSignals,
    evaluate,
    needs_emergency_pointer,
)
from services.grounding import (
    CALLBACK_CORRECTION,
    PROMISED_CALLBACK,
    UNCONFIRMED_BOOKING,
    UNVERIFIED_TIME,
    TurnEvidence,
    claims_team_notified,
    grounding_problem,
)
from services.knowledge import FaqSnippet
from services.lifecycle import (
    InteractionLifecycle,
    InteractionNotFoundError,
    LifecycleStore,
    NoSummarizer,
    primary_intent,
    record_intent,
)
from services.prompt_builder import (
    BusinessFacts,
    Channel,
    HoursFacts,
    Persona,
    PromptContext,
    Tone,
    build,
)
from services.redaction import redact
from services.tools import OperationLog, ToolContext, ToolExecutor, ToolOutcome, ToolRegistry
from services.usage import (
    ActiveCounts,
    LimitExceededError,
    UsageGuard,
    UsageKind,
    UsageUnavailableError,
)

logger = logging.getLogger("tether.conversation")

INVALID_OUTPUT_FALLBACK = "Sorry, I didn't quite catch that. Could you say it another way?"
PROVIDER_FALLBACK = "Sorry, I'm having trouble responding right now. Please try again in a moment."
FALLBACK_INTENT = "UNKNOWN"
MAX_RECORDED_FAILURES = 50
# Tool rounds per turn; each round runs at most `tools.MAX_CALLS_PER_ROUND` calls.
MAX_TOOL_ROUNDS = 3
# Distinct FAQs kept per turn (FAQ RESULTS and the ids the guard accepts), bounding the prompt.
MAX_FAQS_PER_TURN = 10
# Local dates from today in the prompt's BUSINESS HOURS; later ones via `get_business_info`.
PROMPT_HOURS_DAYS = 7
# Added to the request when a reply named a time that wasn't offered (one regeneration).
TIME_CORRECTION = (
    "Your previous reply named a time of day that is not one of the offered_times a "
    "check_availability result gave in this turn. Rewrite the reply. Name only offered_times "
    "exactly, or no time at all; to refer to a time the customer asked for that is not "
    'offered, say "that time" instead of repeating it. If you need other times, call '
    "check_availability again."
)
# Added to the request when a reply claimed a booking (or a change) nothing confirmed.
BOOKING_CORRECTION = (
    "Your previous reply said an appointment is booked, confirmed, cancelled or moved, but "
    "no create_booking result in TOOL RESULTS of this turn says confirmed. Rewrite the reply "
    "without saying anything was booked or changed. If a create_booking result says "
    "booking_failed, say the booking could not be completed and nothing was booked."
)

FailureKind = Literal[
    "invalid_output",
    "provider_unavailable",
    "usage_limited",
    "ungrounded",
    "unverified_time",
    "tool_rounds_exhausted",
    "unconfirmed_booking",
    "promised_callback",
    "unconfirmed_handoff",
]


def quota_fallback(business: Business) -> str:
    """The fixed reply when a usage limit is reached (PRD §9.4): no model call,
    and a human follow-up through the business's own contact details. It
    promises nothing the backend hasn't done (no callback is scheduled)."""
    contacts = [c for c in (business.phone, business.contact_email) if c]
    if contacts:
        return (
            "Sorry, I can't help with that right now. Please reach the team directly "
            f"at {' or '.join(contacts)} and someone will help you."
        )
    return (
        "Sorry, I can't help with that right now. "
        f"Please contact {business.name} directly and someone will help you."
    )


def no_info_fallback(business: Business) -> str:
    """The fixed reply when the agent can't ground an answer: the model's reply
    cited no retrieved FAQ, or it kept asking for tools. Nothing invented, and a
    human follow-up through the business's own contacts (no callback promised)."""
    contacts = [c for c in (business.phone, business.contact_email) if c]
    if contacts:
        return (
            "Sorry, I don't have that information. "
            f"You can reach the team at {' or '.join(contacts)}."
        )
    return f"Sorry, I don't have that information. Please contact {business.name} directly."


def availability_fallback(business: Business) -> str:
    """The fixed reply when the agent named a time it couldn't verify, twice. No time
    is offered, and the customer is pointed to the business's own contacts."""
    contacts = [c for c in (business.phone, business.contact_email) if c]
    if contacts:
        return (
            "Sorry, I can't confirm available times right now. "
            f"You can reach the team at {' or '.join(contacts)}."
        )
    return (
        "Sorry, I can't confirm available times right now. "
        f"Please contact {business.name} directly."
    )


def booking_fallback(business: Business) -> str:
    """The fixed reply when the agent claimed a booking (or a change) the backend didn't
    confirm, twice. It confirms nothing and points to the business's own contacts."""
    contacts = [c for c in (business.phone, business.contact_email) if c]
    if contacts:
        return (
            "Sorry, I can't confirm a booking right now. "
            f"You can reach the team at {' or '.join(contacts)}."
        )
    return f"Sorry, I can't confirm a booking right now. Please contact {business.name} directly."


def handoff_fallback(business: Business) -> str:
    """The fixed reply when the agent said the team was told but no escalation exists:
    it claims nothing and promises no callback, and points to the business's own contacts."""
    contacts = [c for c in (business.phone, business.contact_email) if c]
    if contacts:
        return (
            "Sorry, I haven't been able to pass this on. "
            f"Please reach the team directly at {' or '.join(contacts)}."
        )
    return f"Sorry, I haven't been able to pass this on. Please contact {business.name} directly."


class InteractionEndedError(AppError):
    status_code = 409
    code = "interaction_ended"


class ConversationStore(AgentStore, OperationLog, LifecycleStore, Protocol):
    async def lock_admission_and_count(
        self, business_id: uuid.UUID, idle_minutes: int
    ) -> tuple[int, int]:
        """(all businesses, this business) recently active interactions, counted
        under a lock held until the transaction ends (call before creating one)."""
        ...

    async def create_interaction(self, business_id: uuid.UUID, channel: str) -> Interaction: ...
    async def get_interaction(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID, channel: str
    ) -> Interaction | None: ...
    async def lock_interaction(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID, channel: str
    ) -> Interaction | None: ...
    async def append_message(
        self, interaction: Interaction, speaker: str, message_text: str
    ) -> InteractionMessage: ...
    async def list_messages(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID, limit: int | None = None
    ) -> Sequence[InteractionMessage]: ...
    async def save_interaction(self, interaction: Interaction) -> Interaction: ...
    async def checkpoint(self) -> None:
        """Commit what's written so far and end the transaction (called before the model)."""
        ...

    async def operation_facts(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID
    ) -> Sequence[OperationFact]:
        """This interaction's operation-log rows as facts, oldest first (escalation policy)."""
        ...

    def savepoint(self) -> AbstractAsyncContextManager[Any]:
        """A nested unit of work: an error inside rolls back only what ran inside it
        (each tool executor runs in one, so its failure can still be logged)."""
        ...


@dataclass(frozen=True)
class MessageView:
    sequence_number: int
    speaker: str
    text: str
    created_at: datetime
    # True for the fixed fallback lines (the model failed); survives reloads.
    fallback: bool = False


@dataclass(frozen=True)
class TurnResult:
    interaction_id: uuid.UUID
    reply_text: str
    intent: str
    status: str
    ended: bool
    fallback: bool
    # The reply is the quota fallback: a usage limit was reached, no model call made.
    usage_limited: bool
    # Created by this turn: the greeting (new chat), the customer's message, the reply.
    messages: tuple[MessageView, ...]
    # This turn needed a person: the escalation is recorded (once per reason).
    escalated: bool = False


@dataclass(frozen=True)
class Transcript:
    interaction_id: uuid.UUID
    status: str
    ended: bool
    started_at: datetime
    ended_at: datetime | None
    messages: tuple[MessageView, ...]


@dataclass(frozen=True)
class _Outcome:
    turn: AgentTurn | None
    fallback_text: str
    failures: list[dict[str, str]]
    usage_limited: bool = False
    # The fallback told the customer to reach the team, or a tool said a person is
    # needed: flag the interaction for a human.
    needs_human: bool = False
    # `ToolResult.state` from this turn's tools (applied only if the reply is shown).
    state_updates: dict[str, Any] = field(default_factory=dict)


def _view(message: InteractionMessage, state: dict[str, Any] | None) -> MessageView:
    fallback_numbers = (state or {}).get("fallback_messages", [])
    return MessageView(
        sequence_number=message.sequence_number,
        speaker=message.speaker,
        text=redact(message.message_text),  # also rows stored before redaction existed
        created_at=message.created_at,
        fallback=message.speaker == "agent" and message.sequence_number in fallback_numbers,
    )


def _failure(kind: FailureKind, detail: str) -> dict[str, str]:
    # `detail` is a short fixed reason, never raw model or customer text.
    return {"kind": kind, "at": datetime.now(UTC).isoformat(), "detail": detail}


class ConversationEngine:
    def __init__(
        self,
        store: ConversationStore,
        llm: LLMClient,
        tools: ToolRegistry,
        history_limit: int,
        turn_timeout_seconds: float,
        *,
        usage: UsageGuard,
        llm_retry: RetryPolicy = LLM_RETRY,
        directory: BusinessDirectory | None = None,
        lifecycle: InteractionLifecycle | None = None,
        escalations: TurnEscalations | None = None,
    ) -> None:
        """Without a `directory` the prompt has no BUSINESS HOURS, and BUSINESS FACTS
        comes from the tenant's business row (no currency). Without a `lifecycle` a
        conversation closes with no summary (`NoSummarizer`)."""
        self._store = store
        self._llm = llm
        self._tools = tools
        self._history_limit = history_limit
        self._turn_timeout = turn_timeout_seconds
        self._usage = usage
        self._llm_retry = llm_retry
        self._directory = directory
        self._escalations = escalations
        self._lifecycle = lifecycle or InteractionLifecycle(
            store, NoSummarizer(), idle_minutes=usage.interaction_idle_minutes
        )

    async def handle_turn(
        self, tenant: Tenant, interaction_id: uuid.UUID | None, utterance: str, channel: Channel
    ) -> TurnResult:
        business = require_business(tenant)
        created: list[InteractionMessage] = []
        # Card numbers, SSNs and security codes are never stored or sent to the model.
        utterance = redact(utterance)

        if interaction_id is None:
            everyone, mine = await self._store.lock_admission_and_count(
                business.id, self._usage.interaction_idle_minutes
            )
            self._usage.check_admission(ActiveCounts(all_businesses=everyone, business=mine))
            interaction = await self._store.create_interaction(business.id, channel)
            agent = await load_agent(self._store, business)
            # The owner's greeting opens the conversation, verbatim, as agent message 1.
            # The row is brand new and uncommitted, so no one else can touch it yet.
            created.append(await self._store.append_message(interaction, "agent", agent.greeting))
        else:
            interaction = await self._active_interaction(business.id, interaction_id, channel)
            agent = await load_agent(self._store, business)

        history_rows = await self._store.list_messages(
            business.id, interaction.id, self._history_limit
        )
        history = [
            ModelMessage(
                role="model" if row.speaker == "agent" else "user",
                text=redact(row.message_text),  # rows stored before redaction existed
            )
            for row in history_rows
        ]
        history.append(ModelMessage(role="user", text=utterance))
        facts, hours = await self._business_facts(tenant, business)

        context = PromptContext(
            persona=Persona(
                name=agent.name,
                tone=_tone(agent.tone),
                greeting=agent.greeting,
                custom_instructions=agent.custom_instructions,
            ),
            business=facts,
            hours=hours,
            channel=channel,
            history=history,
            state=dict(interaction.state or {}),
            tools=self._tools.declarations(),
            max_tool_rounds=MAX_TOOL_ROUNDS,
        )
        # Own the ids before committing, and end the transaction so no connection
        # sits idle inside one during the (slow) model call. A new chat's
        # interaction and greeting are durable from here on, whatever happens next.
        business_id, interaction_id = business.id, interaction.id
        await self._store.checkpoint()
        outcome = await self._generate(context, tenant, business, interaction_id)

        reply_text = redact(outcome.turn.response_text if outcome.turn else outcome.fallback_text)
        intent = outcome.turn.intent if outcome.turn else FALLBACK_INTENT

        # No lock has been held so far. Take it now, only to number the
        # messages and merge state against whatever other requests wrote.
        locked = await self._store.lock_interaction(business_id, interaction_id, channel)
        if locked is None:
            raise InteractionNotFoundError("Conversation not found.")
        if locked.status != "in_progress":
            raise InteractionEndedError("This conversation has ended.")

        created.append(await self._store.append_message(locked, "customer", utterance))
        agent_message = await self._store.append_message(locked, "agent", reply_text)
        created.append(agent_message)
        self._merge_state(locked, outcome, agent_message.sequence_number)
        if outcome.turn:
            locked.intent = primary_intent((locked.state or {}).get("intents", []))
        # Decided on the state as it is after this merge, still under the interaction
        # lock, so recording is serialized per interaction.
        recorded: Recorded | None = None
        if self._escalations is not None:
            recorded = await self._escalate(tenant, locked, utterance, outcome, agent_message)
        await self._store.save_interaction(locked)

        for failure in outcome.failures:
            logger.warning(
                "model turn failed",
                extra={"interaction_id": str(locked.id), "failure_kind": failure["kind"]},
            )

        if recorded is not None and self._escalations is not None:
            await self._alert(tenant, recorded)

        return TurnResult(
            interaction_id=locked.id,
            reply_text=agent_message.message_text,
            intent=intent,
            status=locked.status,
            ended=False,
            fallback=outcome.turn is None,
            usage_limited=outcome.usage_limited,
            messages=tuple(_view(m, locked.state) for m in created),
            escalated=recorded is not None and recorded.record.status == "open",
        )

    async def _escalate(
        self,
        tenant: Tenant,
        interaction: Interaction,
        utterance: str,
        outcome: _Outcome,
        agent_message: InteractionMessage,
    ) -> Recorded | None:
        """Applies the escalation policy to this turn. On a decision: an urgent turn whose
        reply lacks an emergency pointer gets the fixed `EMERGENCY_REPLY` (before the
        reply is committed), the escalation is recorded in this transaction, and the
        interaction is flagged for a human."""
        assert self._escalations is not None
        turn = outcome.turn
        decision = evaluate(
            interaction.state or {},
            TurnSignals(
                customer_text=utterance,
                intent=turn.intent if turn else None,
                model_requires_human=turn.requires_human if turn else False,
                fallback=turn is None,
            ),
            await self._store.operation_facts(interaction.business_id, interaction.id),
            await self._escalations.rules(tenant),
        )
        if decision is None:
            if turn is not None and claims_team_notified(agent_message.message_text):
                self._withdraw_handoff_claim(require_business(tenant), interaction, agent_message)
            return None
        if needs_emergency_pointer(decision, agent_message.message_text):
            agent_message.message_text = EMERGENCY_REPLY
        recorded = await self._escalations.record(tenant, interaction.id, decision)
        if recorded.record.status == "open":
            # Something is open for a person. A decision that maps to a RESOLVED row (a
            # history-based reason the owner already closed) flags nothing new.
            state: dict[str, Any] = dict(interaction.state or {})
            state["requires_human"] = True
            interaction.state = state
        return recorded

    @staticmethod
    def _withdraw_handoff_claim(
        business: Business, interaction: Interaction, agent_message: InteractionMessage
    ) -> None:
        """The reply said the team was told, but no escalation exists: nobody was. Replace
        it with the fixed line, record why, and flag the interaction for a human."""
        agent_message.message_text = handoff_fallback(business)
        state: dict[str, Any] = dict(interaction.state or {})
        state["failures"] = [
            *state.get("failures", []),
            _failure("unconfirmed_handoff", "no_escalation"),
        ][-MAX_RECORDED_FAILURES:]
        state["fallback_messages"] = [
            *state.get("fallback_messages", []),
            agent_message.sequence_number,
        ]
        state["requires_human"] = True
        interaction.state = state
        logger.warning(
            "model turn failed",
            extra={"interaction_id": str(interaction.id), "failure_kind": "unconfirmed_handoff"},
        )

    async def _alert(self, tenant: Tenant, recorded: Recorded) -> None:
        """Commits the turn, then alerts the owner (a provider call, so outside any
        transaction). A problem here never changes the reply or fails the turn."""
        assert self._escalations is not None
        # The turn's own commit: a failure here must propagate like any turn commit.
        await self._store.checkpoint()
        try:
            await self._escalations.notify(tenant, recorded)
        except Exception as exc:
            logger.warning("escalation alert step failed", extra={"error_type": type(exc).__name__})
            try:
                await self._store.rollback()
            except Exception:
                logger.warning("escalation alert rollback failed")

    async def end(self, tenant: Tenant, interaction_id: uuid.UUID, channel: Channel) -> Transcript:
        # A plain id: a failed summary rolls the session back, expiring the ORM objects.
        business_id = require_business(tenant).id
        await self._lifecycle.end(tenant, interaction_id, channel)
        interaction = await self._store.get_interaction(business_id, interaction_id, channel)
        if interaction is None:
            raise InteractionNotFoundError("Conversation not found.")
        return await self._transcript(business_id, interaction)

    async def transcript(
        self, tenant: Tenant, interaction_id: uuid.UUID, channel: Channel
    ) -> Transcript:
        business = require_business(tenant)
        interaction = await self._store.get_interaction(business.id, interaction_id, channel)
        if interaction is None:
            raise InteractionNotFoundError("Conversation not found.")
        return await self._transcript(business.id, interaction)

    async def _transcript(self, business_id: uuid.UUID, interaction: Interaction) -> Transcript:
        rows = await self._store.list_messages(business_id, interaction.id)
        return Transcript(
            interaction_id=interaction.id,
            status=interaction.status,
            ended=interaction.status != "in_progress",
            started_at=interaction.started_at,
            ended_at=interaction.ended_at,
            messages=tuple(_view(row, interaction.state) for row in rows),
        )

    async def _business_facts(
        self, tenant: Tenant, business: Business
    ) -> tuple[BusinessFacts, HoursFacts | None]:
        """This turn's BUSINESS FACTS and BUSINESS HOURS, read fresh every turn and
        before the first checkpoint (so inside the turn's first transaction)."""
        if self._directory is None:
            facts = BusinessFacts(
                name=business.name,
                industry=business.industry,
                address=business.address,
                timezone=business.timezone,
                phone=business.phone,
                contact_email=business.contact_email,
            )
            return facts, None
        profile = self._directory.profile(tenant)
        facts = BusinessFacts(
            name=profile.name,
            industry=profile.industry,
            address=profile.address,
            timezone=profile.timezone,
            phone=profile.phone,
            contact_email=profile.contact_email,
            currency=profile.currency,
        )
        now = self._directory.now(tenant)
        today = now.date()
        hours = HoursFacts(
            today=today,
            weekly=await self._directory.weekly_hours(tenant),
            upcoming=await self._directory.schedule(tenant, today, PROMPT_HOURS_DAYS),
            now=now.time().replace(tzinfo=None),
        )
        return facts, hours

    async def _active_interaction(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID, channel: str
    ) -> Interaction:
        interaction = await self._store.get_interaction(business_id, interaction_id, channel)
        if interaction is None:
            raise InteractionNotFoundError("Conversation not found.")
        if interaction.status != "in_progress":
            raise InteractionEndedError("This conversation has ended.")
        return interaction

    async def _call_model(
        self, request: ModelRequest, business_id: uuid.UUID, interaction_id: uuid.UUID
    ) -> ModelReply:
        """One metered model call; transient provider failures retried per `LLM_RETRY`."""

        async def attempt() -> ModelReply:
            async with self._usage.metered(
                UsageKind.GEMINI_REQUEST, business_id=business_id, interaction_id=interaction_id
            ) as meter:
                try:
                    reply = await self._llm.generate(request)
                except LLMUnavailableError as exc:
                    meter.record(tokens_in=exc.tokens_in, tokens_out=exc.tokens_out)
                    raise
                meter.record(tokens_in=reply.tokens_in, tokens_out=reply.tokens_out)
                return reply

        # `LimitExceededError` is not retryable: a reached limit ends the turn at once.
        return await retry_bounded(attempt, self._llm_retry, is_retryable)

    async def _generate(
        self,
        context: PromptContext,
        tenant: Tenant,
        business: Business,
        interaction_id: uuid.UUID,
    ) -> _Outcome:
        """All model work for one turn, tool rounds included, under a single deadline.

        Bounded on every axis: at most one invalid-output retry and
        `MAX_TOOL_ROUNDS` tool rounds, so at most `MAX_TOOL_ROUNDS + 2` model
        calls (each metered, each retried only per `LLM_RETRY`).
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._turn_timeout
        failures: list[dict[str, str]] = []
        executor = ToolExecutor(
            self._tools,
            self._store,
            isolate=self._store.savepoint,
            checkpoint=self._store.checkpoint,
        )
        tool_context = ToolContext(tenant=tenant, interaction_id=interaction_id)
        # FAQs retrieved this turn, by id (FAQ RESULTS), and everything the reply may
        # cite or quote: listed hours that are in the prompt, plus what tools verified.
        retrieved: dict[str, FaqSnippet] = {}
        evidence = TurnEvidence(
            hours_listed=context.hours is not None and context.hours.listed,
            hour_times=_prompt_hour_times(context),
            earlier_offer_times=earlier_offer(context.state)[0],
        )
        results: list[ToolOutcome] = []
        state_updates: dict[str, Any] = {}
        tool_needs_human = False
        # What a tool really did this turn, stated by the backend (`ToolResult.confirmation`).
        confirmation: str | None = None
        rounds = 0
        retried = False
        current = context
        request = build(current)

        def finish(
            turn: AgentTurn | None,
            text: str = "",
            *,
            needs_human: bool = False,
            usage_limited: bool = False,
        ) -> _Outcome:
            if turn is None and confirmation is not None:
                text = confirmation  # never deny what the backend really did
            return _Outcome(
                turn,
                text,
                failures,
                usage_limited=usage_limited,
                needs_human=needs_human or tool_needs_human,
                state_updates=state_updates,
            )

        try:
            while True:
                try:
                    async with asyncio.timeout_at(deadline):
                        reply = await self._call_model(request, business.id, interaction_id)
                except LimitExceededError as exc:
                    failures.append(_failure("usage_limited", f"{exc.kind.value}:{exc.scope}"))
                    return finish(
                        None, quota_fallback(business), usage_limited=True, needs_human=True
                    )
                except LLMUnavailableError:
                    failures.append(_failure("provider_unavailable", "llm_unavailable"))
                    return finish(None, PROVIDER_FALLBACK)
                except UsageUnavailableError:
                    # Limits can't be checked: fail closed, no model call.
                    failures.append(_failure("provider_unavailable", "usage_unavailable"))
                    return finish(None, PROVIDER_FALLBACK)

                turn, reason = self._validate(reply.text)
                if turn is None:
                    failures.append(_failure("invalid_output", reason))
                    if retried:
                        return finish(None, INVALID_OUTPUT_FALLBACK)
                    retried = True
                    if loop.time() >= deadline:
                        raise TimeoutError  # out of time: don't start a retry
                    continue  # the same request, once

                if not turn.tool_calls:
                    problem = grounding_problem(turn, evidence)
                    if problem is None:
                        return finish(turn)
                    if problem in _CORRECTABLE:
                        kind, correction, fallback = _CORRECTABLE[problem]
                        failures.append(_failure(kind, problem))
                        if retried or loop.time() >= deadline:
                            return finish(None, fallback(business), needs_human=True)
                        retried = True
                        request = build(replace(current, correction=correction))
                        continue  # one regeneration, with the correction note
                    failures.append(_failure("ungrounded", problem))
                    return finish(None, no_info_fallback(business), needs_human=True)

                if rounds == MAX_TOOL_ROUNDS:
                    failures.append(_failure("tool_rounds_exhausted", "max_tool_rounds"))
                    return finish(None, no_info_fallback(business), needs_human=True)
                if loop.time() >= deadline:
                    raise TimeoutError  # out of time: don't start another round

                rounds += 1
                outcomes = await executor.run_round(tool_context, turn.tool_calls)
                # Op-log rows are durable before the model hears about them, and no
                # transaction stays open across the next model call.
                await self._store.checkpoint()
                results.extend(outcomes)
                for outcome in outcomes:
                    for snippet in outcome.faqs:
                        if len(retrieved) < MAX_FAQS_PER_TURN and str(snippet.id) not in retrieved:
                            retrieved[str(snippet.id)] = snippet
                            evidence.add_faq(snippet)
                    evidence.add(outcome.evidence)
                    if outcome.status == "succeeded":
                        for key, value in outcome.state.items():
                            # A later "nothing found" doesn't erase this turn's offer.
                            if value is not None or state_updates.get(key) is None:
                                state_updates[key] = value
                        tool_needs_human = tool_needs_human or outcome.requires_human
                        confirmation = outcome.confirmation or confirmation
                current = replace(
                    context,
                    faqs=tuple(retrieved.values()),
                    tool_results=tuple(results),
                    tool_rounds_used=rounds,
                )
                request = build(current)
        except TimeoutError:
            failures.append(_failure("provider_unavailable", "turn_timeout"))
            return finish(None, PROVIDER_FALLBACK)

    def _validate(self, raw: str) -> tuple[AgentTurn | None, str]:
        try:
            turn = AgentTurn.model_validate_json(raw)
        except ValidationError as exc:
            kinds = {error["type"] for error in exc.errors()}
            return None, "json_invalid" if "json_invalid" in kinds else "schema_invalid"
        if any(not self._tools.has(call.name) for call in turn.tool_calls):
            return None, "unregistered_tool"
        return turn, ""

    @staticmethod
    def _merge_state(interaction: Interaction, outcome: _Outcome, reply_sequence: int) -> None:
        # Assign a new dict so the JSONB column registers the change.
        state: dict[str, Any] = dict(interaction.state or {})
        if outcome.failures:
            failures = [*state.get("failures", []), *outcome.failures]
            state["failures"] = failures[-MAX_RECORDED_FAILURES:]
        if outcome.turn is None:
            # The customer never heard what the tools found (e.g. offered slots).
            state.update(dict.fromkeys(outcome.state_updates))
            state["fallback_messages"] = [*state.get("fallback_messages", []), reply_sequence]
            if outcome.needs_human:
                # The customer was told to reach the team: flag it for a human.
                state["requires_human"] = True
            if outcome.usage_limited:
                state["usage_limited_messages"] = [
                    *state.get("usage_limited_messages", []),
                    reply_sequence,
                ]
            if outcome.fallback_text == INVALID_OUTPUT_FALLBACK or any(
                f["kind"] in ("ungrounded", "tool_rounds_exhausted") for f in outcome.failures
            ):
                state["clarification_attempts"] = int(state.get("clarification_attempts", 0)) + 1
        else:
            entities = dict(state.get("entities") or {})
            entities.update(outcome.turn.entities.model_dump(exclude_none=True))
            state["entities"] = entities
            state["intents"] = record_intent(state.get("intents", []), outcome.turn.intent)
            state.update(outcome.state_updates)
            state["requires_human"] = outcome.turn.requires_human or outcome.needs_human
            # Consecutive failed turns: a reply the customer actually got ends the streak.
            state.pop("clarification_attempts", None)
        interaction.state = state


# Guard problems the model gets one chance to fix (sharing the single retry), with the
# note added to the request and the fixed line used if it fails again.
_CORRECTABLE: dict[str, tuple[FailureKind, str, Callable[[Business], str]]] = {
    UNVERIFIED_TIME: ("unverified_time", TIME_CORRECTION, availability_fallback),
    UNCONFIRMED_BOOKING: ("unconfirmed_booking", BOOKING_CORRECTION, booking_fallback),
    PROMISED_CALLBACK: ("promised_callback", CALLBACK_CORRECTION, no_info_fallback),
}


def _prompt_hour_times(context: PromptContext) -> set[time]:
    """Opening and closing times the prompt's BUSINESS HOURS states (weekly and the next
    days, whose real local times can differ on a DST day)."""
    if context.hours is None or not context.hours.listed:
        return set()
    times = {
        moment
        for day in context.hours.weekly
        for moment in (day.opens_at, day.closes_at)
        if moment is not None and not day.is_closed
    }
    for window in context.hours.upcoming:
        if isinstance(window, Open):
            times |= {
                window.start.time().replace(tzinfo=None),
                window.end.time().replace(tzinfo=None),
            }
    return times


def _tone(value: str) -> Tone:
    if value not in ("friendly", "professional", "concise"):
        raise ValueError("Unknown tone stored for agent.")
    return value
