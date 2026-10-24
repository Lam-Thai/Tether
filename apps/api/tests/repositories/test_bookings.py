"""`bookings` repository against real Postgres: the schema-level double-booking guard
(overlap exclusion, buffer, active-only), the active idempotency key, conditional
status changes, the stale-pending sweep and business scoping."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError

from core.db import async_session_factory
from models.booking import Booking, KeyConflict, NewBooking, SlotConflict
from models.interaction import Interaction
from models.service import Service
from repositories import bookings as repo

START = datetime(2031, 3, 7, 15, 0, tzinfo=UTC)


async def _service(session, business_id, name="Haircut") -> Service:
    service = Service(id=uuid.uuid4(), business_id=business_id, name=name, duration_minutes=45)
    session.add(service)
    await session.flush()
    return service


async def _interaction(session, business_id) -> Interaction:
    interaction = Interaction(id=uuid.uuid4(), business_id=business_id, channel="test_chat")
    session.add(interaction)
    await session.flush()
    return interaction


def _new(
    service: Service,
    start: datetime = START,
    *,
    minutes: int = 45,
    buffer: int = 0,
    key: str | None = None,
    interaction_id: uuid.UUID | None = None,
) -> NewBooking:
    end = start + timedelta(minutes=minutes)
    return NewBooking(
        interaction_id=interaction_id,
        service_id=service.id,
        service_name=service.name,
        timezone="America/New_York",
        customer_name="Alex Synthetic",
        customer_phone=None,
        start_time=start,
        end_time=end,
        blocked_until=end + timedelta(minutes=buffer),
        idempotency_key=key or uuid.uuid4().hex,
    )


async def test_overlapping_active_bookings_are_rejected(two_tenants):
    tenant_a, _ = two_tenants
    async with async_session_factory() as session:
        service = await _service(session, tenant_a.business_id)
        first = await repo.reserve(session, tenant_a.business_id, _new(service))
        # Starts 30 minutes in: overlaps the first 45-minute booking.
        clash = await repo.reserve(
            session, tenant_a.business_id, _new(service, START + timedelta(minutes=30))
        )
        # Starts exactly when the first ends: ranges are half-open, so no overlap.
        adjacent = await repo.reserve(
            session, tenant_a.business_id, _new(service, START + timedelta(minutes=45))
        )
        await session.commit()

    assert isinstance(first, Booking) and first.status == "pending"
    assert isinstance(clash, SlotConflict)
    assert isinstance(adjacent, Booking)


async def test_the_buffer_holds_between_two_bookings(two_tenants):
    tenant_a, _ = two_tenants
    async with async_session_factory() as session:
        service = await _service(session, tenant_a.business_id)
        await repo.reserve(session, tenant_a.business_id, _new(service, buffer=15))
        too_close = await repo.reserve(
            session, tenant_a.business_id, _new(service, START + timedelta(minutes=50))
        )
        after_buffer = await repo.reserve(
            session, tenant_a.business_id, _new(service, START + timedelta(minutes=60))
        )
        await session.commit()

    assert isinstance(too_close, SlotConflict)
    assert isinstance(after_buffer, Booking)


async def test_inactive_bookings_free_the_slot(two_tenants):
    tenant_a, _ = two_tenants
    async with async_session_factory() as session:
        service = await _service(session, tenant_a.business_id)
        first = await repo.reserve(session, tenant_a.business_id, _new(service))
        assert isinstance(first, Booking)
        failed = await repo.resolve_pending(
            session, tenant_a.business_id, first.id, status="failed", failure_reason="unavailable"
        )
        again = await repo.reserve(session, tenant_a.business_id, _new(service))
        await session.commit()

    assert failed is not None and failed.status == "failed"
    assert failed.failure_reason == "unavailable"
    assert isinstance(again, Booking)


async def test_other_businesses_never_conflict(two_tenants):
    tenant_a, tenant_b = two_tenants
    async with async_session_factory() as session:
        service_a = await _service(session, tenant_a.business_id)
        service_b = await _service(session, tenant_b.business_id)
        a = await repo.reserve(session, tenant_a.business_id, _new(service_a, key="same"))
        b = await repo.reserve(session, tenant_b.business_id, _new(service_b, key="same"))
        await session.commit()

    assert isinstance(a, Booking) and isinstance(b, Booking)


async def test_an_active_idempotency_key_is_unique(two_tenants):
    tenant_a, _ = two_tenants
    async with async_session_factory() as session:
        service = await _service(session, tenant_a.business_id)
        first = await repo.reserve(session, tenant_a.business_id, _new(service, key="k1"))
        later = START + timedelta(days=1)
        duplicate = await repo.reserve(
            session, tenant_a.business_id, _new(service, later, key="k1")
        )
        found = await repo.find_active_by_key(session, tenant_a.business_id, "k1")
        assert isinstance(first, Booking)
        await repo.resolve_pending(session, tenant_a.business_id, first.id, status="failed")
        gone = await repo.find_active_by_key(session, tenant_a.business_id, "k1")
        # A failed attempt stays as history; the key can be used again.
        retried = await repo.reserve(session, tenant_a.business_id, _new(service, later, key="k1"))
        await session.commit()

    assert isinstance(duplicate, KeyConflict)
    assert found is not None and found.id == first.id
    assert gone is None
    assert isinstance(retried, Booking)


async def test_resolve_pending_only_moves_pending_rows_of_the_business(two_tenants):
    tenant_a, tenant_b = two_tenants
    async with async_session_factory() as session:
        service = await _service(session, tenant_a.business_id)
        booking = await repo.reserve(session, tenant_a.business_id, _new(service))
        assert isinstance(booking, Booking)
        foreign = await repo.resolve_pending(
            session, tenant_b.business_id, booking.id, status="failed"
        )
        confirmed = await repo.resolve_pending(
            session,
            tenant_a.business_id,
            booking.id,
            status="confirmed",
            calendar_event_id="evt-1",
        )
        again = await repo.resolve_pending(
            session, tenant_a.business_id, booking.id, status="failed"
        )
        await session.commit()

    assert foreign is None
    assert confirmed is not None
    assert (confirmed.status, confirmed.calendar_event_id) == ("confirmed", "evt-1")
    assert again is None


async def test_a_confirmed_booking_needs_its_event_id(two_tenants):
    tenant_a, _ = two_tenants
    async with async_session_factory() as session:
        service = await _service(session, tenant_a.business_id)
        booking = await repo.reserve(session, tenant_a.business_id, _new(service))
        assert isinstance(booking, Booking)
        await session.commit()
        with pytest.raises(IntegrityError):
            await repo.resolve_pending(
                session, tenant_a.business_id, booking.id, status="confirmed"
            )
        await session.rollback()


async def test_stale_pending_rows_become_needs_reconciliation(two_tenants):
    tenant_a, tenant_b = two_tenants
    async with async_session_factory() as session:
        service = await _service(session, tenant_a.business_id)
        service_b = await _service(session, tenant_b.business_id)
        stale = await repo.reserve(session, tenant_a.business_id, _new(service))
        fresh = await repo.reserve(
            session, tenant_a.business_id, _new(service, START + timedelta(hours=2))
        )
        other = await repo.reserve(session, tenant_b.business_id, _new(service_b))
        assert isinstance(stale, Booking) and isinstance(fresh, Booking)
        assert isinstance(other, Booking)
        await session.execute(
            update(Booking)
            .where(Booking.id.in_([stale.id, other.id]))
            .values(created_at=text("clock_timestamp() - interval '10 minutes'"))
        )
        expired = await repo.expire_stale_pending(session, tenant_a.business_id, 300)
        # The slot is free again for a new booking.
        rebook = await repo.reserve(session, tenant_a.business_id, _new(service))
        await session.commit()
        await session.refresh(stale)
        await session.refresh(fresh)
        await session.refresh(other)

    assert expired == 1
    assert (stale.status, stale.failure_reason) == ("needs_reconciliation", "pending_timeout")
    assert fresh.status == "pending"
    assert other.status == "pending"  # another business's row is never touched
    assert isinstance(rebook, Booking)


async def test_list_for_interaction_is_scoped(two_tenants):
    tenant_a, tenant_b = two_tenants
    async with async_session_factory() as session:
        service = await _service(session, tenant_a.business_id)
        interaction = await _interaction(session, tenant_a.business_id)
        first = await repo.reserve(
            session, tenant_a.business_id, _new(service, interaction_id=interaction.id)
        )
        second = await repo.reserve(
            session,
            tenant_a.business_id,
            _new(service, START + timedelta(hours=3), interaction_id=interaction.id),
        )
        await repo.reserve(session, tenant_a.business_id, _new(service, START + timedelta(days=2)))
        await session.commit()

        mine = await repo.list_for_interaction(session, tenant_a.business_id, interaction.id)
        theirs = await repo.list_for_interaction(session, tenant_b.business_id, interaction.id)

    assert isinstance(first, Booking) and isinstance(second, Booking)
    assert [b.id for b in mine] == [first.id, second.id]
    assert theirs == []
