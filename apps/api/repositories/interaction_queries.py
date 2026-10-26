"""Read queries behind the owner's inbox (bound to the service port in `core/inbox.py`).

Every query starts from `business_scoped_select` with the business resolved from the
tenant, so another business's interaction is simply not found. The list is keyset
paginated over `(started_at DESC, id DESC)`, served by
`ix_interactions_business_started_id`; the filters AND together:

- `type=bookings`: intent is a booking intent, or the outcome is `booked`
- `type=faqs`: intent is an information intent
- `type=escalations`: outcome `escalated`, or the state flags a human
- `type=failed`: status `failed`
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import ColumnElement, exists, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from models.booking import Booking
from models.interaction import Interaction, InteractionMessage
from models.operation import InteractionOperation
from repositories.base import business_scoped_select

BOOKING_INTENTS = ("BOOK_APPOINTMENT", "RESCHEDULE", "CANCEL")
FAQ_INTENTS = ("FAQ", "BUSINESS_HOURS", "LOCATION", "PRICE_INQUIRY")


def _has_confirmed_booking() -> ColumnElement[bool]:
    """A confirmed booking of this business made in this interaction (it may have been
    confirmed after the interaction closed, so `outcome` alone can lag behind)."""
    return exists().where(
        Booking.business_id == Interaction.business_id,
        Booking.interaction_id == Interaction.id,
        Booking.status == "confirmed",
    )


def _type_clause(kind: str) -> ColumnElement[bool] | None:
    if kind == "bookings":
        return or_(
            Interaction.intent.in_(BOOKING_INTENTS),
            Interaction.outcome == "booked",
            _has_confirmed_booking(),
        )
    if kind == "faqs":
        return Interaction.intent.in_(FAQ_INTENTS)
    if kind == "escalations":
        return or_(
            Interaction.outcome == "escalated",
            Interaction.state.contains({"requires_human": True}),
        )
    if kind == "failed":
        return Interaction.status == "failed"
    return None


async def list_interactions(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    kind: str = "all",
    status: str | None = None,
    intent: str | None = None,
    starts_at: datetime | None = None,
    ends_before: datetime | None = None,
    after: tuple[datetime, uuid.UUID] | None = None,
    limit: int,
) -> Sequence[tuple[Interaction, bool]]:
    """Newest first, each with whether a confirmed booking exists for it.
    `after` is the `(started_at, id)` of the last row already seen.
    Returns up to `limit` rows (the caller asks for one more to know if there is a next page)."""
    query = business_scoped_select(Interaction, business_id).add_columns(
        _has_confirmed_booking().label("has_booking")
    )
    if (clause := _type_clause(kind)) is not None:
        query = query.where(clause)
    if status:
        query = query.where(Interaction.status == status)
    if intent:
        query = query.where(Interaction.intent == intent)
    if starts_at is not None:
        query = query.where(Interaction.started_at >= starts_at)
    if ends_before is not None:
        query = query.where(Interaction.started_at < ends_before)
    if after is not None:
        query = query.where(tuple_(Interaction.started_at, Interaction.id) < tuple_(*after))
    result = await session.execute(
        query.order_by(Interaction.started_at.desc(), Interaction.id.desc()).limit(limit)
    )
    return [(row[0], bool(row[1])) for row in result.all()]


async def get_interaction_for_inbox(
    session: AsyncSession, business_id: uuid.UUID, interaction_id: uuid.UUID
) -> tuple[Interaction, bool] | None:
    result = await session.execute(
        business_scoped_select(Interaction, business_id)
        .add_columns(_has_confirmed_booking().label("has_booking"))
        .where(Interaction.id == interaction_id)
        # A summary written by another session since this one loaded the row.
        .execution_options(populate_existing=True)
    )
    row = result.one_or_none()
    return (row[0], bool(row[1])) if row else None


async def count_messages(
    session: AsyncSession, business_id: uuid.UUID, interaction_id: uuid.UUID
) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(InteractionMessage)
        .where(
            InteractionMessage.business_id == business_id,
            InteractionMessage.interaction_id == interaction_id,
        )
    )
    return int(count or 0)


async def list_operation_rows(
    session: AsyncSession, business_id: uuid.UUID, interaction_id: uuid.UUID
) -> Sequence[InteractionOperation]:
    result = await session.execute(
        business_scoped_select(InteractionOperation, business_id)
        .where(InteractionOperation.interaction_id == interaction_id)
        .order_by(InteractionOperation.created_at, InteractionOperation.id)
    )
    return result.scalars().all()


async def list_messages_after(
    session: AsyncSession,
    business_id: uuid.UUID,
    interaction_id: uuid.UUID,
    after: int,
    limit: int,
) -> Sequence[InteractionMessage]:
    """Messages with `sequence_number > after`, oldest first, at most `limit`."""
    result = await session.execute(
        business_scoped_select(InteractionMessage, business_id)
        .where(
            InteractionMessage.interaction_id == interaction_id,
            InteractionMessage.sequence_number > after,
        )
        .order_by(InteractionMessage.sequence_number)
        .limit(limit)
    )
    return result.scalars().all()
