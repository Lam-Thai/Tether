"""Repository for `usage_counters` and `usage_events`.

`reserve` is where race safety lives: each counter is bumped with
`INSERT ... ON CONFLICT DO UPDATE SET count = count + n WHERE count + n <= limit`.
Postgres re-checks the `WHERE` against the latest committed row while
holding its lock, so N concurrent reservations at the boundary let exactly
`limit` through. The global counter is always bumped before the business
one (one lock order everywhere, so no deadlocks); the caller commits on
"ok" and rolls back on a refusal, so a reservation is all or nothing.

Business-scoped reads and writes take the business id the caller resolved
from `TenantContext`. `business_id=None` means the global (all-businesses)
counter. The `UsageStore` adapter that binds these to sessions is
`core/usage.py`'s `SqlUsageStore`.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.usage import UsageCounter, UsageEvent


async def bump_counter(
    session: AsyncSession,
    business_id: uuid.UUID | None,
    kind: str,
    window: date,
    units: int,
    limit: int | None,
) -> bool:
    """Add `units` to one counter unless that would pass `limit`. True if it was added."""
    if limit is not None and units > limit:
        return False
    if business_id is None:
        conflict_cols = ["kind", "window_date"]
        scope: ColumnElement[bool] = UsageCounter.business_id.is_(None)
    else:
        conflict_cols = ["business_id", "kind", "window_date"]
        scope = UsageCounter.business_id.is_not(None)
    stmt = insert(UsageCounter).values(
        business_id=business_id, kind=kind, window_date=window, count=units
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=conflict_cols,
        index_where=scope,
        set_={"count": UsageCounter.count + units, "updated_at": func.now()},
        where=(UsageCounter.count + units <= limit) if limit is not None else None,
    ).returning(UsageCounter.id)
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def reserve(
    session: AsyncSession,
    *,
    business_id: uuid.UUID | None,
    kind: str,
    window: date,
    units: int,
    global_limit: int | None,
    business_limit: int | None,
) -> Literal["ok", "global", "business"]:
    """Bump the global, then the business counter. Nothing is committed here."""
    if not await bump_counter(session, None, kind, window, units, global_limit):
        return "global"
    if business_id is not None and not await bump_counter(
        session, business_id, kind, window, units, business_limit
    ):
        return "business"
    return "ok"


def add_event(
    session: AsyncSession,
    *,
    business_id: uuid.UUID | None,
    interaction_id: uuid.UUID | None,
    provider: str,
    event_type: str,
    units: int,
    estimated_cost: Decimal,
    created_at: datetime,
) -> None:
    session.add(
        UsageEvent(
            id=uuid.uuid4(),
            business_id=business_id,
            interaction_id=interaction_id,
            provider=provider,
            event_type=event_type,
            units=units,
            estimated_cost=estimated_cost,
            created_at=created_at,
        )
    )


async def get_counters(
    session: AsyncSession, business_id: uuid.UUID | None, window: date
) -> dict[str, int]:
    owner = (
        UsageCounter.business_id.is_(None)
        if business_id is None
        else UsageCounter.business_id == business_id
    )
    rows = await session.execute(
        select(UsageCounter.kind, UsageCounter.count).where(
            owner, UsageCounter.window_date == window
        )
    )
    return {kind: count for kind, count in rows.all()}


async def sum_estimated_cost(
    session: AsyncSession, business_id: uuid.UUID, start: datetime, end: datetime
) -> Decimal:
    total = await session.execute(
        select(func.coalesce(func.sum(UsageEvent.estimated_cost), 0)).where(
            UsageEvent.business_id == business_id,
            UsageEvent.created_at >= start,
            UsageEvent.created_at < end,
        )
    )
    return Decimal(total.scalar_one())
