"""Queries behind account deletion (issue #50). Scoped by the `user_id` / `business_id`
resolved from `TenantContext`; nothing global is ever deleted.

`delete_account` runs in the CALLER's transaction, so it is all or nothing. Deleting a
business cascades (every tenant table is `ON DELETE CASCADE`) to its interactions, messages,
operations, escalations, voice turns, bookings, services, FAQs, hours, booking rules, agent,
calendar connection, phone numbers, subscription, media and its usage events and counters.
Usage rows with `business_id IS NULL` (the global counters) are not under any business and
stay.
"""

import uuid
from datetime import datetime

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models.billing import Subscription
from models.booking import Booking
from models.business import Business
from models.user import User
from repositories.base import business_scoped_select

EVENT_STATUSES = ("confirmed", "needs_reconciliation")
# One transaction with a cascading DELETE over every tenant table, which can outlast the
# connection's default 5 s statement timeout. `SET LOCAL` raises it for this transaction only.
DELETE_STATEMENT_TIMEOUT = "20s"


async def stripe_customer_id(session: AsyncSession, business_id: uuid.UUID) -> str | None:
    return await session.scalar(
        business_scoped_select(Subscription, business_id).with_only_columns(
            Subscription.stripe_customer_id
        )
    )


async def upcoming_event_ids(
    session: AsyncSession, business_id: uuid.UUID, from_time: datetime
) -> list[str]:
    """Google event ids of bookings that may still be on the owner's calendar and have not
    started yet. Past events are the owner's to keep."""
    result = await session.execute(
        business_scoped_select(Booking, business_id)
        .with_only_columns(Booking.calendar_event_id)
        .where(
            Booking.status.in_(EVENT_STATUSES),
            Booking.calendar_event_id.is_not(None),
            Booking.start_time >= from_time,
        )
        .order_by(Booking.start_time, Booking.id)
    )
    return [event_id for event_id in result.scalars().all() if event_id is not None]


async def delete_account(
    session: AsyncSession, user_id: uuid.UUID, upcoming_from: datetime
) -> tuple[int, int, list[str]]:
    """Deletes every business owned by this user (with everything under it) and the user.
    Returns (businesses deleted, bookings deleted, Google event ids of the deleted bookings
    that may still be on the calendar and start at or after `upcoming_from`)."""
    await session.execute(text(f"SET LOCAL statement_timeout = '{DELETE_STATEMENT_TIMEOUT}'"))
    owned = select(Business.id).where(Business.owner_id == user_id)
    # Lock the businesses first: a booking insert takes a key-share lock on its business row,
    # so none can commit between the bookings DELETE below (whose RETURNING reports the events
    # left on the calendar) and the businesses DELETE (whose cascade would drop it unreported).
    await session.execute(owned.with_for_update())
    bookings = (
        await session.execute(
            delete(Booking)
            .where(Booking.business_id.in_(owned))
            .returning(Booking.calendar_event_id, Booking.status, Booking.start_time)
        )
    ).all()
    businesses = await session.execute(delete(Business).where(Business.owner_id == user_id))
    await session.execute(delete(User).where(User.id == user_id))
    event_ids = [
        event_id
        for event_id, status, start in bookings
        if event_id is not None and status in EVENT_STATUSES and start >= upcoming_from
    ]
    return businesses.rowcount, len(bookings), event_ids  # type: ignore[attr-defined]
