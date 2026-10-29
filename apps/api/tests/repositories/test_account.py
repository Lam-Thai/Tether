"""The account-deletion queries on real Postgres: which bookings count as "upcoming", that
every query is scoped to the one user / business, and what `delete_account` reports."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from core.db import async_session_factory
from models.booking import Booking
from models.business import Business
from models.user import User
from repositories import account as repo

CUTOFF = datetime(2031, 6, 1, 12, tzinfo=UTC)


async def _booking(
    business_id: uuid.UUID, start: datetime, event: str | None, status: str = "confirmed"
) -> None:
    async with async_session_factory() as session:
        session.add(
            Booking(
                id=uuid.uuid4(),
                business_id=business_id,
                service_name="Haircut",
                timezone="UTC",
                calendar_event_id=event,
                customer_name="Alex Synthetic",
                start_time=start,
                end_time=start + timedelta(minutes=45),
                blocked_until=start + timedelta(minutes=45),
                status=status,
                idempotency_key=uuid.uuid4().hex,
            )
        )
        await session.commit()


async def _user_id(external_auth_id: str) -> uuid.UUID:
    async with async_session_factory() as session:
        return (
            await session.execute(select(User.id).where(User.external_auth_id == external_auth_id))
        ).scalar_one()


async def _seed(business_id: uuid.UUID, prefix: str) -> None:
    await _booking(business_id, CUTOFF - timedelta(hours=1), f"{prefix}-just-past")
    await _booking(business_id, CUTOFF, f"{prefix}-at-cutoff")
    await _booking(
        business_id, CUTOFF + timedelta(days=1), f"{prefix}-reconcile", "needs_reconciliation"
    )
    await _booking(business_id, CUTOFF + timedelta(days=2), f"{prefix}-cancelled", "cancelled")
    await _booking(business_id, CUTOFF + timedelta(days=3), f"{prefix}-failed", "failed")
    await _booking(business_id, CUTOFF + timedelta(days=4), None, "failed")  # no event to cancel


async def test_upcoming_events_are_this_businesses_active_events_from_the_cutoff(two_tenants):
    tenant, other = two_tenants
    await _seed(tenant.business_id, "a")
    await _seed(other.business_id, "b")

    async with async_session_factory() as session:
        ids = await repo.upcoming_event_ids(session, tenant.business_id, CUTOFF)

    assert ids == ["a-at-cutoff", "a-reconcile"]  # oldest first; the cutoff itself counts


async def test_deleting_an_account_removes_that_users_rows_only_and_reports_them(two_tenants):
    tenant, other = two_tenants
    await _seed(tenant.business_id, "a")
    await _seed(other.business_id, "b")
    user_id = await _user_id(tenant.external_auth_id)

    async with async_session_factory() as session:
        businesses, bookings, event_ids = await repo.delete_account(session, user_id, CUTOFF)
        await session.commit()

    assert (businesses, bookings) == (1, 6)
    assert event_ids == ["a-at-cutoff", "a-reconcile"]  # not cancelled / failed / past
    async with async_session_factory() as session:
        assert await session.get(User, user_id) is None
        assert await session.get(Business, tenant.business_id) is None
        left = (
            await session.scalars(
                select(Booking.calendar_event_id).where(Booking.business_id == other.business_id)
            )
        ).all()
        assert await session.get(Business, other.business_id) is not None
    assert len(left) == 6


async def test_deleting_a_user_with_no_business_removes_just_the_user(two_tenants):
    tenant, other = two_tenants
    async with async_session_factory() as session:
        stray = User(external_auth_id=f"user_{uuid.uuid4().hex}", email="stray@example.com")
        session.add(stray)
        await session.commit()
        stray_id = stray.id

    async with async_session_factory() as session:
        result = await repo.delete_account(session, stray_id, CUTOFF)
        await session.commit()

    assert result == (0, 0, [])
    async with async_session_factory() as session:
        assert await session.get(User, stray_id) is None
        assert await session.get(Business, tenant.business_id) is not None
        assert await session.get(Business, other.business_id) is not None
