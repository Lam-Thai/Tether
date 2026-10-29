"""The rate-limit counters against a real Postgres: races at the limit, windows, purge."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from core.db import async_session_factory
from core.rate_limit import SqlRateLimitStore
from models.rate_limit import RateLimitWindow
from repositories import rate_limit as repo

WINDOW = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)


def _key() -> str:
    return f"probe:{uuid.uuid4()}"


async def test_parallel_hits_at_the_limit_never_exceed_it():
    store = SqlRateLimitStore(async_session_factory)
    key = _key()

    results = await asyncio.gather(*[store.increment(key, WINDOW, 5) for _ in range(20)])

    assert results.count(True) == 5
    async with async_session_factory() as session:
        count = await session.scalar(
            select(RateLimitWindow.count).where(RateLimitWindow.key == key)
        )
    assert count == 5


async def test_windows_and_keys_count_independently():
    store = SqlRateLimitStore(async_session_factory)
    key, other = _key(), _key()

    assert await store.increment(key, WINDOW, 1) is True
    assert await store.increment(key, WINDOW, 1) is False
    assert await store.increment(key, WINDOW + timedelta(minutes=1), 1) is True
    assert await store.increment(other, WINDOW, 1) is True


async def test_a_zero_limit_counts_nothing():
    store = SqlRateLimitStore(async_session_factory)
    key = _key()

    assert await store.increment(key, WINDOW, 0) is False
    async with async_session_factory() as session:
        assert (
            await session.scalar(select(RateLimitWindow).where(RateLimitWindow.key == key)) is None
        )


async def test_purge_deletes_only_windows_older_than_the_cutoff():
    store = SqlRateLimitStore(async_session_factory)
    old, fresh = _key(), _key()
    ancient = datetime(2000, 1, 1, tzinfo=UTC)
    now = datetime.now(UTC).replace(microsecond=0)
    await store.increment(old, ancient, 5)
    await store.increment(fresh, now, 5)

    # The cutoff sits between the two rows; other tests' rows are unaffected either way.
    await store.purge(ancient + timedelta(days=1))

    async with async_session_factory() as session:
        keys = set(
            (
                await session.scalars(
                    select(RateLimitWindow.key).where(RateLimitWindow.key.in_([old, fresh]))
                )
            ).all()
        )
    assert keys == {fresh}


async def test_purge_is_bounded_per_call():
    async with async_session_factory() as session:
        stale = datetime(1999, 1, 1, tzinfo=UTC)
        session.add_all(
            RateLimitWindow(key=f"bulk:{uuid.uuid4()}", window_start=stale, count=1)
            for _ in range(repo.PURGE_BATCH + 10)
        )
        await session.commit()

    async with async_session_factory() as session:
        deleted = await repo.purge_before(session, datetime(1999, 6, 1, tzinfo=UTC))
        await session.commit()

    assert 0 < deleted <= repo.PURGE_BATCH
