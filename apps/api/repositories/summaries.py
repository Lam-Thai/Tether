"""`SqlSummaryStore`: the Postgres side of `services/summarizer.py`.

Reads the stored facts of one closed interaction (transcript, operation log, its
bookings) and writes the summary. Every query is scoped by `business_id`, so another
business's interaction id is simply not found. `claim` is the lease: one conditional
UPDATE, so two concurrent summarize calls cannot both pass it. `save` only fills an empty
summary, so a stored one is never overwritten. `checkpoint` commits the session.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.booking import Booking
from models.interaction import Interaction, InteractionMessage
from models.operation import InteractionOperation
from repositories.base import business_scoped_select
from services.summarizer import (
    BookingFact,
    InteractionFacts,
    OperationFact,
    Summary,
    TranscriptLine,
)

# The newest messages are what a summary is about; a longer chat is trimmed at the
# start (the summarizer keeps even fewer).
MAX_MESSAGES = 80
MAX_OPERATIONS = 40
_SPEAKERS = {"agent", "customer"}


class SqlSummaryStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def load(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID
    ) -> InteractionFacts | None:
        result = await self._session.execute(
            business_scoped_select(Interaction, business_id).where(Interaction.id == interaction_id)
        )
        interaction = result.scalar_one_or_none()
        if interaction is None:
            return None
        await self._session.refresh(interaction)  # a concurrent call may have saved
        messages = (
            await self._session.execute(
                business_scoped_select(InteractionMessage, business_id)
                .where(InteractionMessage.interaction_id == interaction_id)
                .order_by(InteractionMessage.sequence_number.desc())
                .limit(MAX_MESSAGES)
            )
        ).scalars()
        operations = (
            await self._session.execute(
                business_scoped_select(InteractionOperation, business_id)
                .where(InteractionOperation.interaction_id == interaction_id)
                .order_by(InteractionOperation.created_at, InteractionOperation.id)
                .limit(MAX_OPERATIONS)
            )
        ).scalars()
        bookings = (
            await self._session.execute(
                business_scoped_select(Booking, business_id)
                .where(Booking.interaction_id == interaction_id)
                .order_by(Booking.created_at, Booking.id)
            )
        ).scalars()
        return InteractionFacts(
            status=interaction.status,
            outcome=interaction.outcome,
            intent=interaction.intent,
            requires_human=interaction.state.get("requires_human") is True,
            summary=interaction.summary,
            summary_source=interaction.summary_source,
            messages=tuple(
                TranscriptLine(speaker, m.message_text)  # type: ignore[arg-type]
                for m in reversed(list(messages))
                if (speaker := m.speaker) in _SPEAKERS
            ),
            operations=tuple(
                OperationFact(op.tool_name, op.status, _result_code(op.result)) for op in operations
            ),
            bookings=tuple(
                BookingFact(b.service_name, b.start_time, b.timezone, b.status) for b in bookings
            ),
        )

    async def claim(
        self,
        business_id: uuid.UUID,
        interaction_id: uuid.UUID,
        *,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        result = await self._session.execute(
            update(Interaction)
            .where(
                Interaction.id == interaction_id,
                Interaction.business_id == business_id,
                Interaction.status != "in_progress",
                Interaction.summary.is_(None),
                or_(
                    Interaction.summary_claimed_at.is_(None),
                    Interaction.summary_claimed_at < now - timedelta(seconds=lease_seconds),
                ),
            )
            .values(summary_claimed_at=now)
        )
        return result.rowcount == 1  # type: ignore[attr-defined]

    async def save(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID, summary: Summary
    ) -> bool:
        result = await self._session.execute(
            update(Interaction)
            .where(
                Interaction.id == interaction_id,
                Interaction.business_id == business_id,
                Interaction.status != "in_progress",
                Interaction.summary.is_(None),
            )
            .values(summary=summary.text, summary_source=summary.source)
        )
        return result.rowcount == 1  # type: ignore[attr-defined]

    async def checkpoint(self) -> None:
        await self._session.commit()


def _result_code(result: dict[str, object]) -> str | None:
    """The operation log's machine result code, only if it is a short slug."""
    code = result.get("result")
    if isinstance(code, str) and 0 < len(code) <= 40 and code.replace("_", "").isalnum():
        return code
    return None
