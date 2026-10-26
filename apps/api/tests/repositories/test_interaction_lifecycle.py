"""SQL side of the lifecycle: the closing facts and the idle lock."""

import asyncio
import uuid
from datetime import timedelta

from core.db import async_session_factory
from models.booking import Booking
from repositories import interactions as repo
from tests.api.inbox_factory import BASE, add_interaction, utc


async def _booking(business_id, interaction_id, status, day=30):
    async with async_session_factory() as session:
        session.add(
            Booking(
                id=uuid.uuid4(),
                business_id=business_id,
                interaction_id=interaction_id,
                service_name="Haircut",
                timezone="America/New_York",
                customer_name="Alex",
                start_time=BASE + timedelta(days=day),
                end_time=BASE + timedelta(days=day, minutes=45),
                blocked_until=BASE + timedelta(days=day, minutes=45),
                status=status,
                calendar_event_id="ev" if status == "confirmed" else None,
                idempotency_key=str(uuid.uuid4()),
            )
        )
        await session.commit()


async def test_closing_signals_read_bookings_and_the_operation_log(two_tenants):
    tenant_a, tenant_b = two_tenants
    plain = await add_interaction(tenant_a.business_id)
    booked = await add_interaction(tenant_a.business_id)
    await _booking(tenant_a.business_id, booked, "confirmed")
    failed_booking = await add_interaction(
        tenant_a.business_id,
        operations=(("create_booking", "succeeded", {"result": "booking_failed"}),),
    )
    only_pending = await add_interaction(tenant_a.business_id)
    await _booking(tenant_a.business_id, only_pending, "pending", day=31)

    async with async_session_factory() as session:
        got = {
            name: await repo.closing_signals(session, tenant_a.business_id, i)
            for name, i in [
                ("plain", plain),
                ("booked", booked),
                ("failed", failed_booking),
                ("pending", only_pending),
            ]
        }
        # A different business asking about this interaction learns nothing.
        foreign = await repo.closing_signals(session, tenant_b.business_id, booked)

    assert got == {
        "plain": (False, False),
        "booked": (True, False),
        "failed": (False, True),
        "pending": (False, False),
    }
    assert foreign == (False, False)


async def test_lock_idle_is_scoped_bounded_and_skips_rows_another_request_holds(two_tenants):
    tenant_a, tenant_b = two_tenants
    old = utc(2026, 1, 1)
    ids = [
        await add_interaction(
            tenant_a.business_id,
            status="in_progress",
            outcome=None,
            started_at=old + timedelta(minutes=n),
            updated_at=old + timedelta(minutes=n),
        )
        for n in range(4)
    ]
    await add_interaction(
        tenant_b.business_id, status="in_progress", outcome=None, started_at=old, updated_at=old
    )
    await add_interaction(
        tenant_a.business_id, status="in_progress", outcome=None, updated_at=utc(2200, 1, 1)
    )

    async with async_session_factory() as holder, async_session_factory() as other:
        held = await repo.lock_idle(holder, tenant_a.business_id, 15, 1)
        assert [i.id for i in held] == [ids[0]]
        # A concurrent sweep skips the locked row instead of waiting for it.
        skipping = await asyncio.wait_for(repo.lock_idle(other, tenant_a.business_id, 15, 10), 2)
        assert {i.id for i in skipping} == set(ids[1:])
        await other.rollback()
        bounded = await repo.lock_idle(other, tenant_a.business_id, 15, 2)
        assert len(bounded) == 2
        await holder.rollback()
        await other.rollback()
