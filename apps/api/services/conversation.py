"""`ConversationEngine`: one customer turn in, one validated agent reply out.

Small surface (`handle_turn`, `end`, `transcript`) over the rules that keep a
conversation correct:

- Stateless between calls. Everything the next turn needs (messages, entities,
  failures) is read from the store; nothing is cached in the process.
- The owner's persona is reloaded from the store on every turn, so an edit
  applies to the very next message with no restart.
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
- Grounding guard. A final reply with intent `FAQ`, `BUSINESS_HOURS` or
  `PRICE_INQUIRY` (`GROUNDED_INTENTS`) must cite `sources`, and
  every cited id (whatever the intent) must be an FAQ that `search_faq`
  returned in this turn. Otherwise the reply is replaced by the no-info
  fallback and the interaction is flagged for a human. Earlier turns never
  ground a later one: the owner may have edited or disabled the FAQ since.
- The fallback lines below (invalid output, provider, `quota_fallback`,
  `no_info_fallback`) are the only agent text that does not come from the
  model or from the owner's greeting. The last two point the customer to the
  business's own contact details and never promise a callback.
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
from collections.abc import Collection, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
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
from services.knowledge import FaqSnippet
from services.prompt_builder import BusinessFacts, Channel, Persona, PromptContext, Tone, build
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
# Intents whose answer states facts that only FAQs hold (BUSINESS FACTS has no hours,
# prices or policies), so the reply must cite FAQs retrieved this turn. LOCATION is
# answerable from BUSINESS FACTS; the hours/services slices extend this with their tools.
GROUNDED_INTENTS = frozenset({"FAQ", "BUSINESS_HOURS", "PRICE_INQUIRY"})

FailureKind = Literal[
    "invalid_output",
    "provider_unavailable",
    "usage_limited",
    "ungrounded",
    "tool_rounds_exhausted",
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


class InteractionNotFoundError(AppError):
    status_code = 404
    code = "interaction_not_found"


class InteractionEndedError(AppError):
    status_code = 409
    code = "interaction_ended"


class ConversationStore(AgentStore, OperationLog, Protocol):
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
    # The fallback told the customer to reach the team: flag the interaction for a human.
    needs_human: bool = False


def _view(message: InteractionMessage, state: dict[str, Any] | None) -> MessageView:
    fallback_numbers = (state or {}).get("fallback_messages", [])
    return MessageView(
        sequence_number=message.sequence_number,
        speaker=message.speaker,
        text=message.message_text,
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
    ) -> None:
        self._store = store
        self._llm = llm
        self._tools = tools
        self._history_limit = history_limit
        self._turn_timeout = turn_timeout_seconds
        self._usage = usage
        self._llm_retry = llm_retry

    async def handle_turn(
        self, tenant: Tenant, interaction_id: uuid.UUID | None, utterance: str, channel: Channel
    ) -> TurnResult:
        business = require_business(tenant)
        created: list[InteractionMessage] = []

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
            ModelMessage(role="model" if row.speaker == "agent" else "user", text=row.message_text)
            for row in history_rows
        ]
        history.append(ModelMessage(role="user", text=utterance))

        context = PromptContext(
            persona=Persona(
                name=agent.name,
                tone=_tone(agent.tone),
                greeting=agent.greeting,
                custom_instructions=agent.custom_instructions,
            ),
            business=BusinessFacts(
                name=business.name,
                industry=business.industry,
                address=business.address,
                timezone=business.timezone,
                phone=business.phone,
                contact_email=business.contact_email,
            ),
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

        reply_text = outcome.turn.response_text if outcome.turn else outcome.fallback_text
        intent = outcome.turn.intent if outcome.turn else FALLBACK_INTENT

        # No lock has been held so far. Take it now, only to number the
        # messages and merge state against whatever other requests wrote.
        locked = await self._store.lock_interaction(business_id, interaction_id, channel)
        if locked is None:
            raise InteractionNotFoundError("Conversation not found.")
        if locked.status != "active":
            raise InteractionEndedError("This conversation has ended.")

        created.append(await self._store.append_message(locked, "customer", utterance))
        agent_message = await self._store.append_message(locked, "agent", reply_text)
        created.append(agent_message)
        self._merge_state(locked, outcome, agent_message.sequence_number)
        if outcome.turn:
            locked.intent = outcome.turn.intent
        await self._store.save_interaction(locked)

        for failure in outcome.failures:
            logger.warning(
                "model turn failed",
                extra={"interaction_id": str(locked.id), "failure_kind": failure["kind"]},
            )

        return TurnResult(
            interaction_id=locked.id,
            reply_text=reply_text,
            intent=intent,
            status=locked.status,
            ended=False,
            fallback=outcome.turn is None,
            usage_limited=outcome.usage_limited,
            messages=tuple(_view(m, locked.state) for m in created),
        )

    async def end(self, tenant: Tenant, interaction_id: uuid.UUID, channel: Channel) -> Transcript:
        business = require_business(tenant)
        locked = await self._store.lock_interaction(business.id, interaction_id, channel)
        if locked is None:
            raise InteractionNotFoundError("Conversation not found.")
        if locked.status == "active":
            locked.status = "ended"
            locked.ended_at = datetime.now(UTC)
            await self._store.save_interaction(locked)
        return await self._transcript(business.id, locked)

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
            ended=interaction.status == "ended",
            started_at=interaction.started_at,
            ended_at=interaction.ended_at,
            messages=tuple(_view(row, interaction.state) for row in rows),
        )

    async def _active_interaction(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID, channel: str
    ) -> Interaction:
        interaction = await self._store.get_interaction(business_id, interaction_id, channel)
        if interaction is None:
            raise InteractionNotFoundError("Conversation not found.")
        if interaction.status != "active":
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
        executor = ToolExecutor(self._tools, self._store, isolate=self._store.savepoint)
        tool_context = ToolContext(tenant=tenant, interaction_id=interaction_id)
        # FAQs retrieved this turn, by id: the only ones the reply may cite.
        retrieved: dict[str, FaqSnippet] = {}
        results: list[ToolOutcome] = []
        rounds = 0
        retried = False
        request = build(context)
        try:
            while True:
                try:
                    async with asyncio.timeout_at(deadline):
                        reply = await self._call_model(request, business.id, interaction_id)
                except LimitExceededError as exc:
                    failures.append(_failure("usage_limited", f"{exc.kind.value}:{exc.scope}"))
                    return _Outcome(
                        None,
                        quota_fallback(business),
                        failures,
                        usage_limited=True,
                        needs_human=True,
                    )
                except LLMUnavailableError:
                    failures.append(_failure("provider_unavailable", "llm_unavailable"))
                    return _Outcome(None, PROVIDER_FALLBACK, failures)
                except UsageUnavailableError:
                    # Limits can't be checked: fail closed, no model call.
                    failures.append(_failure("provider_unavailable", "usage_unavailable"))
                    return _Outcome(None, PROVIDER_FALLBACK, failures)

                turn, reason = self._validate(reply.text)
                if turn is None:
                    failures.append(_failure("invalid_output", reason))
                    if retried:
                        return _Outcome(None, INVALID_OUTPUT_FALLBACK, failures)
                    retried = True
                    if loop.time() >= deadline:
                        raise TimeoutError  # out of time: don't start a retry
                    continue  # the same request, once

                if not turn.tool_calls:
                    problem = _grounding_problem(turn, retrieved)
                    if problem is None:
                        return _Outcome(turn, "", failures)
                    failures.append(_failure("ungrounded", problem))
                    return _Outcome(None, no_info_fallback(business), failures, needs_human=True)

                if rounds == MAX_TOOL_ROUNDS:
                    failures.append(_failure("tool_rounds_exhausted", "max_tool_rounds"))
                    return _Outcome(None, no_info_fallback(business), failures, needs_human=True)
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
                        if len(retrieved) < MAX_FAQS_PER_TURN:
                            retrieved.setdefault(str(snippet.id), snippet)
                request = build(
                    replace(
                        context,
                        faqs=tuple(retrieved.values()),
                        tool_results=tuple(results),
                        tool_rounds_used=rounds,
                    )
                )
        except TimeoutError:
            failures.append(_failure("provider_unavailable", "turn_timeout"))
            return _Outcome(None, PROVIDER_FALLBACK, failures)

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
            state["fallback_messages"] = [*state.get("fallback_messages", []), reply_sequence]
            if outcome.needs_human:
                # The customer was told to reach the team: flag it for a human.
                state["requires_human"] = True
            if outcome.usage_limited:
                state["usage_limited_messages"] = [
                    *state.get("usage_limited_messages", []),
                    reply_sequence,
                ]
            if outcome.fallback_text == INVALID_OUTPUT_FALLBACK:
                state["clarification_attempts"] = int(state.get("clarification_attempts", 0)) + 1
        else:
            entities = dict(state.get("entities") or {})
            entities.update(outcome.turn.entities.model_dump(exclude_none=True))
            state["entities"] = entities
            state["requires_human"] = outcome.turn.requires_human
        interaction.state = state


def _grounding_problem(turn: AgentTurn, retrieved: Collection[str]) -> str | None:
    """Why `turn` isn't grounded in this turn's FAQ results, as a fixed code (or None)."""
    cited = {source.strip().lower() for source in turn.sources}
    if turn.intent in GROUNDED_INTENTS and not cited:
        return "missing_sources"
    if not cited <= set(retrieved):
        return "unknown_source"
    return None


def _tone(value: str) -> Tone:
    if value not in ("friendly", "professional", "concise"):
        raise ValueError("Unknown tone stored for agent.")
    return value
