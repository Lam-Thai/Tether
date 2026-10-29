"""Queries behind "delete test data" (slice 16). Every statement is scoped by the
`business_id` resolved from `TenantContext`; nothing global is ever deleted.

`erase_business_test_data` runs in the CALLER's transaction (the router commits), so it is
all or nothing. Deleting interactions cascades to their messages, operations, escalations
and voice turns (every `escalations` row belongs to an interaction, so none can remain).
"""

import uuid
from datetime import date

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models.booking import Booking
from models.escalation import Escalation
from models.interaction import Interaction, InteractionMessage
from models.media import MediaObject
from models.usage import UsageCounter, UsageEvent

EVENT_STATUSES = ("confirmed", "needs_reconciliation")
# The erase is a single transaction with a cascading DELETE on `interactions`, which can
# outlast the connection's default 5 s statement timeout for a business with a lot of
# test data. `SET LOCAL` raises it for this transaction only.
ERASE_STATEMENT_TIMEOUT = "20s"


async def calendar_event_ids(session: AsyncSession, business_id: uuid.UUID) -> list[str]:
    """Google event ids of bookings that may still be on the owner's calendar."""
    result = await session.execute(
        select(Booking.calendar_event_id)
        .where(
            Booking.business_id == business_id,
            Booking.status.in_(EVENT_STATUSES),
            Booking.calendar_event_id.is_not(None),
        )
        .order_by(Booking.created_at, Booking.id)
    )
    return [event_id for event_id in result.scalars().all() if event_id is not None]


async def _count(session: AsyncSession, model: type, business_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(model).where(model.business_id == business_id)  # type: ignore[attr-defined]
        )
        or 0
    )


async def erase_business_test_data(
    session: AsyncSession, business_id: uuid.UUID, *, keep_usage_from: date
) -> tuple[dict[str, int], list[str]]:
    """Deletes this business's bookings, interactions (+ cascades), usage events, usage
    counters of days BEFORE `keep_usage_from`, and reply audio (`cache_key IS NULL`; cached
    fixed lines stay). Returns the counts removed and the Google event ids of the deleted
    bookings that may still be on the calendar."""
    await session.execute(text(f"SET LOCAL statement_timeout = '{ERASE_STATEMENT_TIMEOUT}'"))
    messages = await _count(session, InteractionMessage, business_id)
    escalations = await _count(session, Escalation, business_id)
    bookings = (
        await session.execute(
            delete(Booking)
            .where(Booking.business_id == business_id)
            .returning(Booking.calendar_event_id, Booking.status)
        )
    ).all()
    interactions = await session.execute(
        delete(Interaction).where(Interaction.business_id == business_id)
    )
    usage_events = await session.execute(
        delete(UsageEvent).where(UsageEvent.business_id == business_id)
    )
    await session.execute(
        delete(UsageCounter).where(
            UsageCounter.business_id == business_id, UsageCounter.window_date < keep_usage_from
        )
    )
    await session.execute(
        delete(MediaObject).where(
            MediaObject.business_id == business_id, MediaObject.cache_key.is_(None)
        )
    )
    event_ids = [
        event_id
        for event_id, status in bookings
        if event_id is not None and status in EVENT_STATUSES
    ]
    counts = {
        "interactions": interactions.rowcount,  # type: ignore[attr-defined]
        "messages": messages,
        "bookings": len(bookings),
        "escalations": escalations,
        "usage_events": usage_events.rowcount,  # type: ignore[attr-defined]
    }
    return counts, event_ids
