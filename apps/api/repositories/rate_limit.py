"""Repository for `rate_limit_windows` (slice 17). Not tenant data: the key already
carries the subject (a business id or a client IP) the caller resolved.

`increment` is race-safe the same way `usage.bump_counter` is: one
`INSERT ... ON CONFLICT DO UPDATE SET count = count + 1 WHERE count < limit`; Postgres
re-checks the `WHERE` against the latest committed row under its lock, so N concurrent
hits at the boundary let exactly `limit` through. The `RateLimitStore` adapter that
gives each call its own committed transaction is `core/rate_limit.SqlRateLimitStore`.
"""

from datetime import datetime

from sqlalchemy import delete, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.rate_limit import RateLimitWindow

PURGE_BATCH = 500


async def increment(session: AsyncSession, key: str, window_start: datetime, limit: int) -> bool:
    """Count one hit in the window unless `limit` is reached. True if it was counted."""
    if limit <= 0:
        return False
    stmt = insert(RateLimitWindow).values(key=key, window_start=window_start, count=1)
    stmt = stmt.on_conflict_do_update(
        index_elements=["key", "window_start"],
        set_={"count": RateLimitWindow.count + 1},
        where=RateLimitWindow.count < limit,
    ).returning(RateLimitWindow.count)
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def purge_before(session: AsyncSession, before: datetime) -> int:
    """Delete up to `PURGE_BATCH` windows that started before `before`; returns how many."""
    stale = (
        select(RateLimitWindow.key, RateLimitWindow.window_start)
        .where(RateLimitWindow.window_start < before)
        .limit(PURGE_BATCH)
    )
    result = await session.execute(
        delete(RateLimitWindow).where(
            tuple_(RateLimitWindow.key, RateLimitWindow.window_start).in_(stale)
        )
    )
    return getattr(result, "rowcount", 0) or 0
