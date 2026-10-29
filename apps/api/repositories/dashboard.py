"""Counts behind the owner's dashboard (slice 16). Every subquery is scoped by `business_id`
(from `TenantContext`); all four numbers come back in ONE round trip.

- `total_interactions`: interactions started in `[starts_at, ends_before)`, any channel.
- `successful_bookings`: `confirmed` bookings created in the range.
- `faqs_answered`: interactions started in the range with an information intent that ended
  `answered`.
- `open_escalations`: the current queue, not range-bound.
"""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.booking import Booking
from models.escalation import Escalation
from models.interaction import Interaction
from repositories.interaction_queries import FAQ_INTENTS


async def dashboard_counts(
    session: AsyncSession, business_id: uuid.UUID, starts_at: datetime, ends_before: datetime
) -> tuple[int, int, int, int]:
    interactions_in_range = (
        Interaction.business_id == business_id,
        Interaction.started_at >= starts_at,
        Interaction.started_at < ends_before,
    )
    total = (
        select(func.count()).select_from(Interaction).where(*interactions_in_range)
    ).scalar_subquery()
    bookings = (
        select(func.count())
        .select_from(Booking)
        .where(
            Booking.business_id == business_id,
            Booking.status == "confirmed",
            Booking.created_at >= starts_at,
            Booking.created_at < ends_before,
        )
    ).scalar_subquery()
    faqs = (
        select(func.count())
        .select_from(Interaction)
        .where(
            *interactions_in_range,
            Interaction.intent.in_(FAQ_INTENTS),
            Interaction.outcome == "answered",
        )
    ).scalar_subquery()
    escalations = (
        select(func.count())
        .select_from(Escalation)
        .where(Escalation.business_id == business_id, Escalation.status == "open")
    ).scalar_subquery()
    row = (await session.execute(select(total, bookings, faqs, escalations))).one()
    return int(row[0]), int(row[1]), int(row[2]), int(row[3])
