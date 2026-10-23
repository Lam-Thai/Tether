"""Booking rules repository against real Postgres: upsert, scoping, constraints."""

from dataclasses import dataclass

import pytest
from sqlalchemy.exc import IntegrityError

from core.db import async_session_factory
from repositories import booking_rules as repo


@dataclass(frozen=True)
class _Values:
    slot_interval_minutes: int
    min_notice_minutes: int
    max_days_ahead: int
    buffer_minutes: int


def _rules(interval=15, notice=120, days=60, buffer=0):
    return _Values(interval, notice, days, buffer)


async def test_upsert_updates_in_place_and_is_scoped(two_tenants):
    tenant_a, tenant_b = two_tenants
    a, b = tenant_a.business_id, tenant_b.business_id

    async with async_session_factory() as session:
        assert await repo.get_booking_rules(session, a) is None
        first = await repo.upsert_booking_rules(session, a, _rules())
        first_id = first.id
        await session.commit()
    async with async_session_factory() as session:
        second = await repo.upsert_booking_rules(session, a, _rules(30, 0, 14, 10))
        await session.commit()
    assert second.id == first_id and second.slot_interval_minutes == 30

    async with async_session_factory() as session:
        mine = await repo.get_booking_rules(session, a)
        assert await repo.get_booking_rules(session, b) is None
    assert mine is not None
    assert (mine.min_notice_minutes, mine.max_days_ahead, mine.buffer_minutes) == (0, 14, 10)


@pytest.mark.parametrize(
    "bad",
    [
        _rules(interval=7),
        _rules(interval=0),
        _rules(interval=245),
        _rules(notice=-1),
        _rules(notice=20161),
        _rules(days=0),
        _rules(days=366),
        _rules(buffer=-5),
        _rules(buffer=245),
    ],
    ids=[
        "interval-not-multiple",
        "interval-zero",
        "interval-high",
        "notice-negative",
        "notice-high",
        "days-zero",
        "days-high",
        "buffer-negative",
        "buffer-high",
    ],
)
async def test_check_constraints_reject_out_of_range(two_tenants, bad):
    tenant_a, _ = two_tenants
    async with async_session_factory() as session:
        with pytest.raises(IntegrityError):
            await repo.upsert_booking_rules(session, tenant_a.business_id, bad)
        await session.rollback()
