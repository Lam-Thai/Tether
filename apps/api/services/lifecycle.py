"""How a conversation ends: status, outcome, primary intent, and the idle sweep.

`close_outcome` and `primary_intent` are pure and exhaustively tested; the rules live
here and nowhere else. `InteractionLifecycle` applies them to a stored interaction and
is shared by every channel (the test chat now, the voice slice later):

- `end` closes an in-progress interaction (derives status + outcome from what the
  backend recorded, never from the model's claims), COMMITS the close, and only then
  asks the summarizer for the two-sentence summary. A summarizer failure never undoes
  the close. Ending an already-closed interaction changes nothing.
- `abandon_idle` marks this business's idle in-progress interactions `abandoned`, with
  a template summary and zero AI calls (`ai=False`).

No FastAPI or SQLAlchemy imports: the caller passes a `LifecycleStore`.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from core.errors import AppError
from core.tenant import Tenant
from models.interaction import Interaction, InteractionMessage
from services.agent import require_business
from services.summarizer import SummarizerPort, Summary

logger = logging.getLogger("tether.lifecycle")

Status = Literal["in_progress", "completed", "failed", "abandoned"]
Outcome = Literal["booked", "answered", "escalated", "failed", "abandoned"]
Channel = Literal["test_chat", "voice"]
CloseReason = Literal["ended", "idle"]

# Idle sweeps are bounded so one inbox read never does unbounded work.
MAX_ABANDONED_PER_SWEEP = 50
MAX_RECORDED_INTENTS = 20

# Most significant first: the conversation's headline intent is the first one present.
INTENT_PRIORITY = (
    "BOOK_APPOINTMENT",
    "RESCHEDULE",
    "CANCEL",
    "URGENT",
    "COMPLAINT",
    "HUMAN_REQUEST",
    "PRICE_INQUIRY",
    "BUSINESS_HOURS",
    "LOCATION",
    "FAQ",
    "UNKNOWN",
)


class InteractionNotFoundError(AppError):
    status_code = 404
    code = "interaction_not_found"


@dataclass(frozen=True)
class ClosingFacts:
    """What the backend recorded about the conversation, not what the model said."""

    has_confirmed_booking: bool
    # A `create_booking` call was logged with the result `booking_failed`.
    booking_failed: bool
    # The last agent message was one of the fixed fallback lines.
    last_agent_message_was_fallback: bool
    requires_human: bool


def close_outcome(facts: ClosingFacts, reason: CloseReason) -> tuple[Status, Outcome]:
    booked = facts.has_confirmed_booking
    status: Status
    if reason == "idle":
        status = "abandoned"
    elif not booked and (facts.booking_failed or facts.last_agent_message_was_fallback):
        status = "failed"
    else:
        status = "completed"

    outcome: Outcome
    if booked:
        outcome = "booked"
    elif status == "failed":
        outcome = "failed"
    elif facts.requires_human:
        outcome = "escalated"
    elif reason == "idle":
        outcome = "abandoned"
    else:
        outcome = "answered"
    return status, outcome


def primary_intent(intents: Sequence[str]) -> str | None:
    """The most significant intent seen in a conversation (None when none was recorded)."""
    seen = set(intents)
    for intent in INTENT_PRIORITY:
        if intent in seen:
            return intent
    return next(iter(intents), None)


def record_intent(state_intents: Sequence[str], intent: str) -> list[str]:
    """`state["intents"]` after a valid turn: distinct, in order of first appearance, capped."""
    if intent in state_intents:
        return list(state_intents)
    return [*state_intents, intent][:MAX_RECORDED_INTENTS]


class NoSummarizer:
    """Stands in until a real summarizer is wired: summaries stay NULL (the inbox shows
    "summary pending" and the detail view retries)."""

    async def summarize(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID, *, ai: bool = True
    ) -> Summary | None:
        return None


class LifecycleStore(Protocol):
    async def lock_interaction(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID, channel: str
    ) -> Interaction | None: ...
    async def list_messages(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID, limit: int | None = None
    ) -> Sequence[InteractionMessage]: ...
    async def save_interaction(self, interaction: Interaction) -> Interaction: ...
    async def checkpoint(self) -> None: ...
    async def rollback(self) -> None: ...
    async def closing_signals(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID
    ) -> tuple[bool, bool]:
        """(has a confirmed booking, a `create_booking` call logged `booking_failed`)."""
        ...

    async def lock_idle(
        self, business_id: uuid.UUID, idle_minutes: int, limit: int
    ) -> Sequence[Interaction]:
        """This business's in-progress interactions idle longer than `idle_minutes`,
        locked (rows another request holds are skipped), at most `limit`."""
        ...


class InteractionLifecycle:
    def __init__(
        self, store: LifecycleStore, summarizer: SummarizerPort, *, idle_minutes: int
    ) -> None:
        self._store = store
        self._summarizer = summarizer
        self._idle_minutes = idle_minutes

    async def end(self, tenant: Tenant, interaction_id: uuid.UUID, channel: Channel) -> None:
        business_id = require_business(tenant).id
        locked = await self._store.lock_interaction(business_id, interaction_id, channel)
        if locked is None:
            raise InteractionNotFoundError("Conversation not found.")
        if locked.status == "in_progress":
            await self._close(locked, "ended", ended_at=datetime.now(UTC))
            await self._store.save_interaction(locked)
        # Durable before any model call; also releases the row lock.
        await self._store.checkpoint()
        await self.summarize_safely(business_id, interaction_id, ai=True)

    async def abandon_idle(self, business_id: uuid.UUID) -> int:
        idle = await self._store.lock_idle(business_id, self._idle_minutes, MAX_ABANDONED_PER_SWEEP)
        closed: list[uuid.UUID] = []
        for interaction in idle:
            await self._close(interaction, "idle", ended_at=interaction.updated_at)
            await self._store.save_interaction(interaction)
            closed.append(interaction.id)
        await self._store.checkpoint()
        for interaction_id in closed:
            await self.summarize_safely(business_id, interaction_id, ai=False)
        return len(closed)

    async def sweep_idle(self, business_id: uuid.UUID) -> int:
        """`abandon_idle` for a read path: a failure is logged (type only), rolled back and
        reported as 0, so the owner still gets the list."""
        try:
            return await self.abandon_idle(business_id)
        except Exception as exc:
            logger.warning("idle sweep failed", extra={"error_type": type(exc).__name__})
            try:
                await self._store.rollback()
            except Exception:
                logger.warning("idle sweep rollback failed")
            return 0

    async def _close(
        self, interaction: Interaction, reason: CloseReason, *, ended_at: datetime
    ) -> None:
        booked, booking_failed = await self._store.closing_signals(
            interaction.business_id, interaction.id
        )
        state = interaction.state or {}
        last = await self._store.list_messages(interaction.business_id, interaction.id, 1)
        last_was_fallback = bool(
            last
            and last[-1].speaker == "agent"
            and last[-1].sequence_number in state.get("fallback_messages", [])
        )
        facts = ClosingFacts(
            has_confirmed_booking=booked,
            booking_failed=booking_failed,
            last_agent_message_was_fallback=last_was_fallback,
            requires_human=bool(state.get("requires_human")),
        )
        interaction.status, interaction.outcome = close_outcome(facts, reason)
        interaction.ended_at = ended_at

    async def summarize_safely(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID, *, ai: bool = True
    ) -> None:
        """Ask for the summary on a path that must keep going (End, the inbox's lazy
        summary): a failure is logged (type only) and the session rolled back, since the
        summarizer shares it and a database error leaves its transaction aborted. The
        rollback expires ORM objects, so callers copy what they still need first."""
        try:
            await self._summarizer.summarize(business_id, interaction_id, ai=ai)
        except Exception as exc:
            # The close is already committed. Never log the exception text: it could
            # quote a transcript.
            logger.warning(
                "summary failed",
                extra={"interaction_id": str(interaction_id), "error_type": type(exc).__name__},
            )
            try:
                await self._store.rollback()
            except Exception:
                logger.warning("summary rollback failed")
