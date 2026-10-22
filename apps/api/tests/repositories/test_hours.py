"""Business hours repository against real Postgres: atomic replace, scoping, constraints."""

from datetime import time

import pytest
from sqlalchemy.exc import IntegrityError

from core.db import async_session_factory
from models.hours import BusinessHours
from repositories import hours as repo


def _week(opens: time, closes: time, closed_days=(0, 6)):
    return [
        (d, True, None, None) if d in closed_days else (d, False, opens, closes) for d in range(7)
    ]


async def test_replace_is_atomic_scoped_and_validated(two_tenants):
    tenant_a, tenant_b = two_tenants
    a, b = tenant_a.business_id, tenant_b.business_id

    async with async_session_factory() as session:
        assert await repo.list_hours(session, a) == []
        first = await repo.replace_week(session, a, _week(time(9), time(17)))
        await repo.replace_week(session, b, _week(time(10), time(14), closed_days=()))
        await session.commit()
    assert [r.day_of_week for r in first] == list(range(7))

    async with async_session_factory() as session:
        second = await repo.replace_week(session, a, _week(time(8), time(12), closed_days=(6,)))
        await session.commit()
    assert [(r.day_of_week, r.is_closed) for r in second][0] == (0, False)
    assert second[0].opens_at == time(8) and second[6].opens_at is None

    # A failing replace rolls back with the transaction: the old week survives.
    async with async_session_factory() as session:
        with pytest.raises(IntegrityError):
            await repo.replace_week(
                session, a, [*_week(time(9), time(17))[:6], (6, False, time(18), time(9))]
            )
        await session.rollback()

    async with async_session_factory() as session:
        mine = await repo.list_hours(session, a)
        theirs = await repo.list_hours(session, b)
    assert len(mine) == 7 and mine[0].opens_at == time(8)
    assert len(theirs) == 7 and all(r.opens_at == time(10) for r in theirs)
    assert {r.business_id for r in mine} == {a}


async def test_duplicate_day_is_rejected(two_tenants):
    tenant_a, _ = two_tenants
    async with async_session_factory() as session:
        session.add_all(
            BusinessHours(business_id=tenant_a.business_id, day_of_week=2, is_closed=True)
            for _ in range(2)
        )
        with pytest.raises(IntegrityError):
            await session.flush()
