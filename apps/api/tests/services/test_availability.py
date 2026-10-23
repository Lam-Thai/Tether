"""`AvailabilityService.find_slots` at its boundary: in-memory directory, a scripted busy
source standing in for the calendar, a pinned clock. No DB, no Google."""

import logging
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from core.tenant import Tenant
from integrations.google_calendar.port import BusyInterval
from models.business import Business
from models.user import User
from services.availability import (
    AvailabilityService,
    CalendarUnavailable,
    NoAvailability,
    SlotOffer,
    SlotPreference,
)
from services.booking_rules import BookingRules
from services.calendar import (
    CalendarAuthRevokedError,
    CalendarNotSelectedError,
    CalendarUnavailableError,
)
from services.catalog import ServiceNotFoundError
from services.directory import BusinessDirectory
from tests.services.fake_busy_source import FakeBusySource, local
from tests.services.memory_directory_store import MemoryDirectoryStore

NY = ZoneInfo("America/New_York")
# Wednesday 2026-10-07, 14:00 in New York.
NOW = datetime(2026, 10, 7, 18, 0, tzinfo=UTC)
WEDNESDAY = date(2026, 10, 7)
FRIDAY = date(2026, 10, 9)
SATURDAY = date(2026, 10, 10)
SUNDAY = date(2026, 10, 11)
WEEK = {
    0: ("09:00", "17:00"),
    1: ("09:00", "17:00"),
    2: ("09:00", "17:00"),
    3: ("09:00", "17:00"),
    4: ("09:00", "17:00"),
    5: ("10:00", "13:00"),
    6: None,
}
DEFAULTS = BookingRules(
    slot_interval_minutes=15, min_notice_minutes=120, max_days_ahead=60, buffer_minutes=0
)


def _at(day: date, hh_mm: str) -> datetime:
    return local(day, hh_mm)


def _tenant(name: str) -> Tenant:
    user = User(id=uuid.uuid4(), external_auth_id=f"user_{name}", email="owner@example.test")
    business = Business(
        id=uuid.uuid4(),
        owner_id=user.id,
        name=name,
        industry="Salon",
        address="12 Elm St",
        timezone="America/New_York",
        phone="+1 555 0100",
        contact_email="hello@example.test",
        currency="USD",
    )
    return Tenant(user=user, business=business)


def _bid(tenant: Tenant) -> uuid.UUID:
    assert tenant.business is not None
    return tenant.business.id


@dataclass
class World:
    tenant: Tenant
    store: MemoryDirectoryStore
    calendar: FakeBusySource
    service: AvailabilityService
    haircut: uuid.UUID


@pytest.fixture
def world() -> World:
    tenant = _tenant("Alpha Salon")
    store = MemoryDirectoryStore()
    store.set_week(_bid(tenant), WEEK)
    haircut = store.add_service(_bid(tenant), "Haircut", duration_minutes=45).id
    calendar = FakeBusySource()
    directory = BusinessDirectory(store, lambda: NOW, DEFAULTS)
    return World(tenant, store, calendar, AvailabilityService(directory, calendar), haircut)


def _labels(slots) -> list[str]:
    return [slot.start.strftime("%H:%M") for slot in slots]


async def _find(world: World, day: date = FRIDAY, **preference) -> object:
    return await world.service.find_slots(
        world.tenant, world.haircut, SlotPreference(day, **preference)
    )


# --- offers --------------------------------------------------------------------------


async def test_offers_spread_free_slots_inside_hours_in_the_business_timezone(world):
    world.calendar.add(_bid(world.tenant), FRIDAY, "09:00", "12:00")
    result = await _find(world, time_of_day="afternoon")

    assert isinstance(result, SlotOffer)
    assert result.timezone == "America/New_York"
    assert result.matched_preference is True
    assert _labels(result.offered) == ["12:00", "14:00", "16:15"]
    assert result.slots[0].start.utcoffset() == timedelta(hours=-4)
    assert set(result.offered) <= set(result.slots)
    # 12:00 .. 16:15 every 15 minutes: the 45-minute service must end by 17:00.
    assert _labels(result.slots)[0] == "12:00" and _labels(result.slots)[-1] == "16:15"


async def test_busy_source_is_asked_for_the_opening_window_widened_by_the_buffer(world):
    world.store.booking_rules[_bid(world.tenant)] = BookingRules(15, 120, 60, buffer_minutes=10)
    await _find(world)
    [(business_id, window)] = world.calendar.calls
    assert business_id == _bid(world.tenant)
    assert window.start == _at(FRIDAY, "08:50") and window.end == _at(FRIDAY, "17:10")


async def test_owner_rules_replace_the_defaults(world):
    world.store.booking_rules[_bid(world.tenant)] = BookingRules(60, 0, 60, 0)
    result = await _find(world)
    assert isinstance(result, SlotOffer)
    assert _labels(result.slots) == [
        "09:00",
        "10:00",
        "11:00",
        "12:00",
        "13:00",
        "14:00",
        "15:00",
        "16:00",
    ]


async def test_around_a_time_offers_the_nearest_free_slots(world):
    world.calendar.add(_bid(world.tenant), FRIDAY, "15:00", "16:00")
    result = await _find(world, around=time(15, 0))
    assert isinstance(result, SlotOffer)
    assert _labels(result.offered) == ["14:00", "14:15", "16:00"]


async def test_nothing_in_the_preferred_part_of_the_day_offers_the_rest(world):
    world.calendar.add(_bid(world.tenant), FRIDAY, "12:00", "17:00")
    result = await _find(world, time_of_day="afternoon")
    assert isinstance(result, SlotOffer)
    assert result.matched_preference is False
    assert all(slot.start.hour < 12 for slot in result.offered)


async def test_today_respects_the_minimum_notice(world):
    result = await _find(world, WEDNESDAY)  # now 14:00, notice 2h
    assert isinstance(result, SlotOffer)
    assert _labels(result.slots) == ["16:00", "16:15"]


# --- no availability -------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "reason"),
    [
        (WEDNESDAY - timedelta(days=1), "past_date"),
        (WEDNESDAY + timedelta(days=61), "beyond_horizon"),
        (SUNDAY, "closed"),
    ],
    ids=["past", "beyond-horizon", "closed"],
)
async def test_dates_the_backend_rules_out_never_reach_the_calendar(world, day, reason):
    result = await _find(world, day)
    assert isinstance(result, NoAvailability)
    assert result.reason == reason
    assert world.calendar.calls == []
    if reason in ("past_date", "beyond_horizon"):
        assert (result.earliest, result.latest) == (WEDNESDAY, WEDNESDAY + timedelta(days=60))


async def test_hours_not_set_is_not_closed(world):
    world.store.hours = []
    result = await _find(world)
    assert isinstance(result, NoAvailability) and result.reason == "hours_not_set"
    assert world.calendar.calls == []


async def test_service_longer_than_the_opening_window(world):
    long = world.store.add_service(_bid(world.tenant), "Colour", duration_minutes=240).id
    result = await world.service.find_slots(world.tenant, long, SlotPreference(SATURDAY))
    assert isinstance(result, NoAvailability) and result.reason == "service_too_long"
    assert world.calendar.calls == []


async def test_no_time_left_today_skips_the_calendar(world):
    world.store.booking_rules[_bid(world.tenant)] = BookingRules(15, 240, 60, 0)
    result = await _find(world, WEDNESDAY)
    assert isinstance(result, NoAvailability) and result.reason == "no_time_left"
    assert world.calendar.calls == []


async def test_a_day_with_no_free_time_is_fully_booked(world):
    world.calendar.add(_bid(world.tenant), FRIDAY, "00:00", "23:59")
    result = await _find(world)
    assert isinstance(result, NoAvailability) and result.reason == "fully_booked"


# --- calendar failures --------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (CalendarNotSelectedError("not_connected"), "not_connected"),
        (CalendarAuthRevokedError("auth_revoked"), "reconnect_needed"),
        (CalendarUnavailableError("server_error"), "unavailable"),
    ],
    ids=["not-connected", "revoked", "outage"],
)
async def test_calendar_failures_offer_no_times(world, error, reason, caplog):
    world.calendar.error = error
    with caplog.at_level(logging.INFO, logger="tether.availability"):
        result = await _find(world)
    assert isinstance(result, CalendarUnavailable)
    assert result.reason == reason
    assert not hasattr(result, "slots")
    [record] = [r for r in caplog.records if r.name == "tether.availability"]
    assert record.outcome == reason and isinstance(record.duration_ms, int)


async def test_latency_is_logged_on_success(world, caplog):
    with caplog.at_level(logging.INFO, logger="tether.availability"):
        await _find(world)
    [record] = [r for r in caplog.records if r.name == "tether.availability"]
    assert record.outcome == "ok" and record.duration_ms >= 0


# --- scoping ----------------------------------------------------------------------


async def test_unknown_inactive_or_other_tenants_services_are_not_found(world):
    other = _tenant("Beta Salon")
    foreign = world.store.add_service(_bid(other), "Haircut").id
    retired = world.store.add_service(_bid(world.tenant), "Perm", active=False).id
    for service_id in (foreign, retired, uuid.uuid4()):
        with pytest.raises(ServiceNotFoundError):
            await world.service.find_slots(world.tenant, service_id, SlotPreference(FRIDAY))
    assert world.calendar.calls == []


async def test_busy_time_and_rules_are_read_for_the_tenant_only(world):
    other = _tenant("Beta Salon")
    world.store.set_week(_bid(other), WEEK)  # replaces nothing of Alpha's
    world.store.set_week(_bid(world.tenant), WEEK)
    world.calendar.add(_bid(other), FRIDAY, "00:00", "23:59")
    world.store.booking_rules[_bid(other)] = BookingRules(60, 0, 1, 0)
    result = await _find(world)
    assert isinstance(result, SlotOffer)
    assert _labels(result.slots)[:2] == ["09:00", "09:15"]
    assert {business_id for business_id, _ in world.calendar.calls} == {_bid(world.tenant)}


# --- the safety property ----------------------------------------------------------


@pytest.mark.parametrize("seed", range(25))
async def test_never_offers_a_time_the_hours_or_the_calendar_do_not_support(world, seed):
    rng = random.Random(seed)
    business = _bid(world.tenant)
    for _ in range(rng.randint(0, 6)):
        start = rng.randint(7 * 60, 18 * 60)
        length = rng.randint(5, 150)
        world.calendar.busy.setdefault(business, []).append(
            BusyInterval(
                _at(FRIDAY, "00:00") + timedelta(minutes=start),
                _at(FRIDAY, "00:00") + timedelta(minutes=start + length),
            )
        )
    buffer = rng.choice([0, 5, 15])
    world.store.booking_rules[business] = BookingRules(rng.choice([5, 15, 30]), 120, 60, buffer)

    result = await _find(world, time_of_day=rng.choice([None, "morning", "afternoon"]))
    if not isinstance(result, SlotOffer):
        assert isinstance(result, NoAvailability) and result.reason == "fully_booked"
        return
    opens, closes = _at(FRIDAY, "09:00"), _at(FRIDAY, "17:00")
    pad = timedelta(minutes=buffer)
    for slot in result.slots:
        start, end = slot.start.astimezone(UTC), slot.end.astimezone(UTC)
        assert opens <= start and end <= closes
        assert end - start == timedelta(minutes=45)
        for block in world.calendar.busy[business] if world.calendar.busy else []:
            assert end <= block.start - pad or start >= block.end + pad
    assert 1 <= len(result.offered) <= 3 and set(result.offered) <= set(result.slots)


async def test_a_date_inside_the_minimum_notice_is_too_soon_not_too_far(world):
    world.store.booking_rules[_bid(world.tenant)] = BookingRules(15, 2880, 60, 0)  # 2 days
    result = await _find(world, WEDNESDAY + timedelta(days=1))
    assert isinstance(result, NoAvailability) and result.reason == "too_soon"
    assert result.earliest == WEDNESDAY + timedelta(days=2)
    assert world.calendar.calls == []
