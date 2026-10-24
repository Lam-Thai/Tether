"""Zero double bookings under real concurrency: several `BookingService.book` calls for
the same (or an overlapping) slot, each on its own Postgres session, all of them past
the availability recheck before any reserves (a barrier in the fake calendar), so the
only thing left to stop a double booking is the schema's exclusion constraint.

Real Postgres, real `SqlBookingStore` / `SqlDirectoryStore`; Google is a fake."""

import asyncio
import uuid
from datetime import UTC, date, datetime, time

from sqlalchemy import select

from core.db import async_session_factory
from core.directory import SqlDirectoryStore
from core.tenant import Tenant
from integrations.google_calendar.port import BusyInterval, TimeWindow
from models.booking import Booking
from models.business import Business
from models.hours import BusinessHours
from models.interaction import Interaction
from models.service import Service
from models.user import User
from repositories.bookings import SqlBookingStore
from services.availability import AvailabilityService
from services.booking import BookingRequest, BookingService, Confirmed, SlotTaken
from services.booking_rules import BookingRules
from services.directory import BusinessDirectory
from tests.services.fake_booking_calendar import FakeBookingCalendar

# Wednesday 2026-10-07, 14:00 in New York; Friday is open 09:00-17:00.
NOW = datetime(2026, 10, 7, 18, 0, tzinfo=UTC)
FRIDAY = date(2026, 10, 9)
RULES = BookingRules(15, 120, 60, 0)


class BarrierCalendar(FakeBookingCalendar):
    """Free/busy waits until every caller has asked, so all of them see the slot free."""

    def __init__(self, callers: int) -> None:
        super().__init__()
        self._callers = callers
        self._arrived = 0
        self._all_in = asyncio.Event()

    async def free_busy(self, business_id: uuid.UUID, window: TimeWindow) -> list[BusyInterval]:
        busy = await super().free_busy(business_id, window)
        self._arrived += 1
        if self._arrived >= self._callers:
            self._all_in.set()
        await asyncio.wait_for(self._all_in.wait(), timeout=10)
        return busy


async def _salon(business_id: uuid.UUID) -> dict[str, uuid.UUID]:
    async with async_session_factory() as session:
        for day in range(7):
            session.add(
                BusinessHours(
                    business_id=business_id,
                    day_of_week=day,
                    is_closed=day >= 5,
                    opens_at=None if day >= 5 else time(9),
                    closes_at=None if day >= 5 else time(17),
                )
            )
        services = {
            "haircut": Service(business_id=business_id, name="Haircut", duration_minutes=45),
            "beard": Service(business_id=business_id, name="Beard trim", duration_minutes=15),
        }
        session.add_all(services.values())
        await session.commit()
        return {name: service.id for name, service in services.items()}


async def _book(
    business_id: uuid.UUID, calendar: FakeBookingCalendar, service_id: uuid.UUID, hh_mm: str
):
    async with async_session_factory() as session:
        business = await session.get(Business, business_id)
        assert business is not None
        owner = await session.get(User, business.owner_id)
        assert owner is not None
        interaction = Interaction(business_id=business_id, channel="test_chat")
        session.add(interaction)
        await session.commit()
        store = SqlBookingStore(session)
        directory = BusinessDirectory(SqlDirectoryStore(session), lambda: NOW, RULES)
        availability = AvailabilityService(directory, calendar, savepoint=store.savepoint)
        service = BookingService(directory, availability, calendar, store)
        return await service.book(
            Tenant(user=owner, business=business),
            BookingRequest(
                interaction_id=interaction.id,
                service_id=service_id,
                date=FRIDAY,
                start_time=time.fromisoformat(hh_mm),
                customer_name=f"Caller {uuid.uuid4().hex[:6]}",
            ),
        )


async def _rows(business_id: uuid.UUID) -> list[Booking]:
    async with async_session_factory() as session:
        result = await session.execute(select(Booking).where(Booking.business_id == business_id))
        return list(result.scalars())


async def test_simultaneous_bookings_for_one_slot_confirm_exactly_one(two_tenants):
    tenant, _ = two_tenants
    services = await _salon(tenant.business_id)
    callers = 5
    calendar = BarrierCalendar(callers)

    outcomes = await asyncio.gather(
        *(_book(tenant.business_id, calendar, services["haircut"], "15:30") for _ in range(callers))
    )

    confirmed = [o for o in outcomes if isinstance(o, Confirmed)]
    assert len(confirmed) == 1
    assert all(isinstance(o, SlotTaken) for o in outcomes if not isinstance(o, Confirmed))
    assert calendar.created == 1 and len(calendar.events_of(tenant.business_id)) == 1
    rows = await _rows(tenant.business_id)
    assert [r.status for r in rows] == ["confirmed"]
    assert rows[0].calendar_event_id == confirmed[0].booking.calendar_event_id


async def test_overlapping_bookings_of_different_services_confirm_exactly_one(two_tenants):
    tenant, _ = two_tenants
    services = await _salon(tenant.business_id)
    calendar = BarrierCalendar(2)

    # A 45-minute haircut at 15:30 and a beard trim at 15:45 overlap (one chair).
    outcomes = await asyncio.gather(
        _book(tenant.business_id, calendar, services["haircut"], "15:30"),
        _book(tenant.business_id, calendar, services["beard"], "15:45"),
    )

    assert sorted(type(o).__name__ for o in outcomes) == ["Confirmed", "SlotTaken"]
    assert calendar.created == 1
    assert [r.status for r in await _rows(tenant.business_id)] == ["confirmed"]
