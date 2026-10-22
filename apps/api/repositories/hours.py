"""Repository for `business_hours`. Every query is scoped by `business_id`."""

import uuid
from collections.abc import Iterable, Sequence
from datetime import time

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from models.hours import BusinessHours
from repositories.base import business_scoped_select


async def list_hours(session: AsyncSession, business_id: uuid.UUID) -> Sequence[BusinessHours]:
    """The stored weekday rows (0 or 7), Monday first."""
    result = await session.execute(
        business_scoped_select(BusinessHours, business_id).order_by(BusinessHours.day_of_week)
    )
    return result.scalars().all()


async def replace_week(
    session: AsyncSession,
    business_id: uuid.UUID,
    days: Iterable[tuple[int, bool, time | None, time | None]],
) -> Sequence[BusinessHours]:
    """Deletes the business's rows and inserts `days` (day_of_week, is_closed, opens_at,
    closes_at) in the caller's transaction, so the week is replaced atomically when the
    router commits and untouched if anything fails first. A per-business advisory lock,
    held until that commit, serializes concurrent PUTs: without it, two deletes would
    each miss the other's inserts and collide on `(business_id, day_of_week)`."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('hours:' || CAST(:b AS text), 0))"),
        {"b": str(business_id)},
    )
    await session.execute(delete(BusinessHours).where(BusinessHours.business_id == business_id))
    session.add_all(
        BusinessHours(
            business_id=business_id,
            day_of_week=day_of_week,
            is_closed=is_closed,
            opens_at=opens_at,
            closes_at=closes_at,
        )
        for day_of_week, is_closed, opens_at, closes_at in days
    )
    await session.flush()
    return await list_hours(session, business_id)
