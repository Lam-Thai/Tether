"""`BookingService.book` at its boundary: in-memory store with the schema's rules, a fake
calendar (free/busy + events), the real `AvailabilityService` and `BusinessDirectory`.
No DB, no Google. The Postgres constraint and real concurrency are covered in
`tests/repositories/test_bookings.py` and `test_booking_concurrency.py`."""

import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest

from core.tenant import Tenant
from models.booking import NewBooking
from models.business import Business
from models.user import User
from services.availability import AvailabilityService
from services.booking import (
    PENDING_TIMEOUT_SECONDS,
    BookingRequest,
    BookingService,
    CalendarFailed,
    Confirmed,
    Invalid,
    SlotTaken,
)
from services.booking_rules import BookingRules
from services.calendar import (
    CalendarAuthRevokedError,
    CalendarNotSelectedError,
    CalendarUnavailableError,
)
from services.directory import BusinessDirectory
from tests.services.fake_booking_calendar import FakeBookingCalendar
from tests.services.fake_busy_source import NEW_YORK, local
from tests.services.memory_booking_store import MemoryBookingStore
from tests.services.memory_directory_store import MemoryDirectoryStore

# Wednesday 2026-10-07, 14:00 in New York.
NOW = datetime(2026, 10, 7, 18, 0, tzinfo=UTC)
FRIDAY = date(2026, 10, 9)
SUNDAY = date(2026, 10, 11)
WEEK = {d: ("09:00", "17:00") for d in range(5)} | {5: None, 6: None}
RULES = BookingRules(
    slot_interval_minutes=15, min_notice_minutes=120, max_days_ahead=60, buffer_minutes=0
)


def _tenant() -> Tenant:
    user = User(id=uuid.uuid4(), external_auth_id="user_book", email="owner@example.com")
    business = Business(
        id=uuid.uuid4(),
        owner_id=user.id,
        name="Alpha Salon",
        industry="Salon",
        address="1 Main St",
        timezone="America/New_York",
        phone="+1 (312) 847-1928",
        contact_email=None,
        currency="USD",
    )
    return Tenant(user=user, business=business)


class Salon:
    def __init__(self, rules: BookingRules = RULES) -> None:
        self.tenant = _tenant()
        assert self.tenant.business is not None
        self.business_id = self.tenant.business.id
        directory_store = MemoryDirectoryStore()
        directory_store.set_week(self.business_id, WEEK)
        self.haircut = directory_store.add_service(
            self.business_id, "Haircut", duration_minutes=45, price="40.00"
        )
        self.calendar = FakeBookingCalendar()
        # Friday morning is taken.
        self.calendar.add_busy(self.business_id, FRIDAY, "09:00", "12:00")
        self.store = MemoryBookingStore()
        directory = BusinessDirectory(directory_store, lambda: NOW, rules)
        availability = AvailabilityService(directory, self.calendar, savepoint=self.store.savepoint)
        self.service = BookingService(directory, availability, self.calendar, self.store)
        self.interaction_id = uuid.uuid4()

    def request(self, hh_mm: str = "15:30", day: date = FRIDAY, **extra) -> BookingRequest:
        values = {
            "interaction_id": self.interaction_id,
            "service_id": self.haircut.id,
            "date": day,
            "start_time": time.fromisoformat(hh_mm),
            "customer_name": "Alex Synthetic",
        } | extra
        return BookingRequest(**values)

    async def book(self, hh_mm: str = "15:30", **extra):
        return await self.service.book(self.tenant, self.request(hh_mm, **extra))


@pytest.fixture
def salon() -> Salon:
    return Salon()


async def _hold(salon: Salon, hh_mm: str, key: str = "other-call") -> uuid.UUID:
    start = local(FRIDAY, hh_mm)
    row = await salon.store.reserve(
        salon.business_id,
        NewBooking(
            interaction_id=uuid.uuid4(),
            service_id=salon.haircut.id,
            service_name="Haircut",
            customer_name="Sam Synthetic",
            customer_phone=None,
            start_time=start,
            end_time=start + timedelta(minutes=45),
            blocked_until=start + timedelta(minutes=45),
            idempotency_key=key,
        ),
    )
    return row.id  # type: ignore[union-attr]


def _starts(slots) -> list[str]:
    return [s.start.astimezone(NEW_YORK).strftime("%H:%M") for s in slots]


# --- the happy path ----------------------------------------------------------------


async def test_books_a_free_slot_with_the_stored_duration(salon):
    outcome = await salon.book("15:30", customer_phone=" +1 (555) 010-0199 ")

    assert isinstance(outcome, Confirmed) and not outcome.replayed
    booking = outcome.booking
    assert booking.start == local(FRIDAY, "15:30")
    assert booking.end - booking.start == timedelta(minutes=45)  # from the stored service
    assert booking.start.tzinfo is not None and booking.start.utcoffset() == timedelta(hours=-4)
    assert (booking.service_name, booking.customer_name) == ("Haircut", "Alex Synthetic")
    [row] = salon.store.of(salon.business_id)
    assert (row.status, row.calendar_event_id) == ("confirmed", booking.calendar_event_id)
    assert row.customer_phone == "+1 (555) 010-0199"
    [event] = salon.calendar.events_of(salon.business_id)
    assert event.id == booking.calendar_event_id
    [draft] = salon.calendar.drafts
    assert draft.summary == "Haircut - Alex Synthetic"
    assert draft.time_zone == "America/New_York"
    assert draft.description is not None and "+1 (555) 010-0199" in draft.description
    # Reserved and committed before Google was called, then confirmed and committed.
    assert salon.store.checkpoints >= 2


async def test_the_same_call_twice_creates_one_event(salon):
    first = await salon.book("15:30")
    again = await salon.book("15:30")

    assert isinstance(first, Confirmed) and isinstance(again, Confirmed)
    assert again.replayed and again.booking.id == first.booking.id
    assert salon.calendar.created == 1
    assert len(salon.store.of(salon.business_id)) == 1


async def test_the_buffer_is_stored_with_the_booking():
    salon = Salon(BookingRules(15, 120, 60, buffer_minutes=10))

    outcome = await salon.book("15:30")

    assert isinstance(outcome, Confirmed)
    [row] = salon.store.of(salon.business_id)
    assert row.blocked_until == row.end_time + timedelta(minutes=10)


# --- the slot isn't free -----------------------------------------------------------


async def test_a_slot_taken_on_the_calendar_offers_alternatives(salon):
    salon.calendar.add_busy(salon.business_id, FRIDAY, "15:00", "16:00")

    outcome = await salon.book("15:30")

    assert isinstance(outcome, SlotTaken)
    assert outcome.alternatives
    assert all(
        s.end <= local(FRIDAY, "15:00") or s.start >= local(FRIDAY, "16:00")
        for s in outcome.alternatives
    )
    assert salon.store.rows == [] and salon.calendar.created == 0


async def test_a_time_off_the_slot_grid_is_not_bookable(salon):
    outcome = await salon.book("15:20")

    assert isinstance(outcome, SlotTaken)
    assert _starts(outcome.alternatives) == ["15:00", "15:15", "15:30"]


async def test_a_pending_reservation_by_another_caller_blocks_the_slot(salon):
    await _hold(salon, "15:00")

    outcome = await salon.book("15:30")

    assert isinstance(outcome, SlotTaken)
    assert "15:30" not in _starts(outcome.alternatives)
    assert salon.calendar.created == 0


async def test_the_same_booking_still_in_flight_is_not_made_twice(salon):
    request = salon.request("15:30")
    key = f"{request.interaction_id}:{request.service_id}:{local(FRIDAY, '15:30').isoformat()}"
    await _hold(salon, "15:30", key=key)

    outcome = await salon.book("15:30")

    assert outcome == CalendarFailed("in_progress")
    assert salon.calendar.created == 0


async def test_a_stale_pending_reservation_no_longer_blocks(salon):
    stale = await _hold(salon, "15:30")
    salon.store.stale_ids.add(stale)

    outcome = await salon.book("15:30")

    assert isinstance(outcome, Confirmed)
    old = next(r for r in salon.store.rows if r.id == stale)
    assert (old.status, old.failure_reason) == ("needs_reconciliation", "pending_timeout")
    assert PENDING_TIMEOUT_SECONDS >= 60


async def test_a_fully_booked_day_has_no_alternatives(salon):
    salon.calendar.add_busy(salon.business_id, FRIDAY, "12:00", "17:00")

    outcome = await salon.book("15:30")

    assert isinstance(outcome, SlotTaken) and outcome.alternatives == ()


# --- invalid requests --------------------------------------------------------------


@pytest.mark.parametrize(
    ("extra", "reason"),
    [
        ({"customer_name": "   "}, "missing_name"),
        ({"customer_name": "x" * 81}, "missing_name"),
        ({"customer_phone": "call me maybe"}, "invalid_phone"),
        ({"customer_phone": "12"}, "invalid_phone"),
        ({"service_id": uuid.UUID(int=7)}, "service_not_found"),
        ({"day": SUNDAY}, "closed"),
    ],
    ids=["blank-name", "long-name", "words-phone", "short-phone", "unknown-service", "closed"],
)
async def test_invalid_requests_book_nothing(salon, extra, reason):
    outcome = await salon.book("15:30", **extra)

    assert isinstance(outcome, Invalid) and outcome.reason == reason
    assert salon.store.rows == [] and salon.calendar.created == 0


async def test_a_past_date_names_the_bookable_range(salon):
    outcome = await salon.book("10:00", day=date(2026, 10, 1))

    assert isinstance(outcome, Invalid) and outcome.reason == "past_date"
    assert outcome.earliest == date(2026, 10, 7)


# --- the calendar fails ------------------------------------------------------------


async def test_a_calendar_that_cannot_be_read_books_nothing(salon):
    salon.calendar.source.error = CalendarNotSelectedError("not_connected")

    outcome = await salon.book("15:30")

    assert isinstance(outcome, CalendarFailed) and outcome.reason == "not_connected"
    assert salon.store.rows == []


async def test_a_revoked_grant_on_create_fails_the_booking(salon):
    salon.calendar.create_error = CalendarAuthRevokedError("auth_revoked")

    outcome = await salon.book("15:30")

    assert isinstance(outcome, CalendarFailed) and outcome.reason == "reconnect_needed"
    [row] = salon.store.rows
    assert (row.status, row.failure_reason) == ("failed", "reconnect_needed")
    assert salon.calendar.cancelled == []  # refused before anything was created


async def test_a_timeout_that_created_the_event_is_undone(salon):
    salon.calendar.create_error = CalendarUnavailableError("timeout")
    salon.calendar.create_lands_before_error = True

    outcome = await salon.book("15:30")

    assert isinstance(outcome, CalendarFailed) and outcome.reason == "unavailable"
    [row] = salon.store.rows
    assert (row.status, row.failure_reason) == ("failed", "unavailable")
    assert salon.calendar.events_of(salon.business_id) == []
    # The slot is free again: the customer can book it once Google is back.
    salon.calendar.create_error = None
    assert isinstance(await salon.book("15:30"), Confirmed)


async def test_an_undo_that_cannot_be_confirmed_needs_reconciliation(salon):
    salon.calendar.create_error = CalendarUnavailableError("timeout")
    salon.calendar.create_lands_before_error = True
    salon.calendar.cancel_error = CalendarUnavailableError("timeout")

    outcome = await salon.book("15:30")

    assert isinstance(outcome, CalendarFailed)
    [row] = salon.store.rows
    assert (row.status, row.failure_reason) == ("needs_reconciliation", "unavailable")


async def test_a_reached_usage_limit_is_not_undone(salon):
    salon.calendar.create_error = CalendarUnavailableError("usage")

    outcome = await salon.book("15:30")

    assert isinstance(outcome, CalendarFailed) and outcome.reason == "unavailable"
    assert salon.store.rows[0].status == "failed"
    assert salon.calendar.cancelled == []


async def test_an_event_that_cannot_be_saved_is_cancelled(salon):
    salon.store.confirm_error = RuntimeError("db down")

    outcome = await salon.book("15:30")

    assert isinstance(outcome, CalendarFailed) and outcome.reason == "not_saved"
    [row] = salon.store.rows
    assert (row.status, row.failure_reason) == ("failed", "not_saved")
    assert salon.calendar.events_of(salon.business_id) == []
    assert len(salon.calendar.cancelled) == 1


async def test_an_unsaved_event_that_cannot_be_cancelled_needs_reconciliation(salon):
    salon.store.confirm_error = RuntimeError("db down")
    salon.calendar.cancel_error = CalendarUnavailableError("timeout")

    outcome = await salon.book("15:30")

    assert outcome == CalendarFailed("not_saved", outcome.service)  # type: ignore[union-attr]
    assert salon.store.rows[0].status == "needs_reconciliation"


# --- reading back ------------------------------------------------------------------


async def test_for_interaction_lists_this_chats_attempts_only(salon):
    salon.calendar.create_error = CalendarAuthRevokedError("auth_revoked")
    await salon.book("15:30")
    salon.calendar.create_error = None
    await salon.book("15:30")
    await salon.service.book(salon.tenant, salon.request("16:15", interaction_id=uuid.uuid4()))

    listed = await salon.service.for_interaction(salon.tenant, salon.interaction_id)
    other = await salon.service.for_interaction(Salon().tenant, salon.interaction_id)

    assert [b.status for b in listed] == ["failed", "confirmed"]
    assert listed[1].start == local(FRIDAY, "15:30")
    assert listed[1].start.utcoffset() == timedelta(hours=-4)
    assert other == []
