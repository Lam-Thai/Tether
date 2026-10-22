"""`BusinessDirectory` at its boundary: in-memory store, pinned clock, no DB."""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from core.tenant import Tenant
from models.business import Business
from models.user import User
from services.agent import BusinessNotFoundError
from services.directory import (
    Ambiguous,
    BusinessDirectory,
    Closed,
    Found,
    HoursNotSet,
    NotFound,
    Open,
)
from tests.services.memory_directory_store import MemoryDirectoryStore

NEW_YORK = "America/New_York"
# 2026-03-08: US clocks spring forward at 02:00. 2026-11-01: they fall back at 02:00.
SPRING_FORWARD = date(2026, 3, 8)
FALL_BACK = date(2026, 11, 1)


def _tenant(name: str = "Alpha Salon", timezone: str = NEW_YORK) -> Tenant:
    user = User(id=uuid.uuid4(), external_auth_id=f"user_{name}", email="owner@example.test")
    business = Business(
        id=uuid.uuid4(),
        owner_id=user.id,
        name=name,
        industry="Salon",
        address="12 Elm St, Springfield",
        timezone=timezone,
        phone="+1 (312) 847-1928",
        contact_email="hello@alpha.test",
        currency="CAD",
    )
    return Tenant(user=user, business=business)


def _id(tenant: Tenant) -> uuid.UUID:
    assert tenant.business is not None
    return tenant.business.id


def _clock(at: datetime):
    return lambda: at


def _directory(store: MemoryDirectoryStore, now: datetime | None = None) -> BusinessDirectory:
    return BusinessDirectory(store, _clock(now or datetime(2026, 10, 21, 15, 0, tzinfo=UTC)))


WEEK = {
    0: None,  # Monday closed
    1: ("09:00", "17:00"),
    2: ("09:00", "17:00"),
    3: ("09:00", "19:00"),
    4: ("09:00", "17:00"),
    5: ("10:00", "16:00"),  # Saturday
    6: None,  # Sunday closed
}


def _minutes(window: Open) -> float:
    return (window.end.astimezone(UTC) - window.start.astimezone(UTC)).total_seconds() / 60


# --- profile ------------------------------------------------------------------


def test_profile_is_the_tenants_own_contact_details():
    tenant = _tenant()
    profile = _directory(MemoryDirectoryStore()).profile(tenant)
    assert profile.name == "Alpha Salon"
    assert profile.address == "12 Elm St, Springfield"
    assert (profile.phone, profile.contact_email) == ("+1 (312) 847-1928", "hello@alpha.test")
    assert (profile.timezone, profile.currency) == (NEW_YORK, "CAD")


def test_every_method_needs_a_business():
    user = User(id=uuid.uuid4(), external_auth_id="x", email="x@example.test")
    with pytest.raises(BusinessNotFoundError):
        _directory(MemoryDirectoryStore()).profile(Tenant(user=user, business=None))


# --- services -------------------------------------------------------------------


async def test_services_are_active_only_by_default_and_scoped_to_the_tenant():
    store = MemoryDirectoryStore()
    tenant, other = _tenant(), _tenant("Beta Barbers")
    store.add_service(_id(tenant), "Haircut", duration_minutes=45, price="40.00")
    store.add_service(_id(tenant), "Hot towel shave", active=False, price="25.00")
    store.add_service(_id(other), "Beard trim", price="15.00")
    directory = _directory(store)

    active = await directory.services(tenant)
    everything = await directory.services(tenant, active_only=False)

    assert [s.name for s in active] == ["Haircut"]
    assert active[0].duration_minutes == 45 and str(active[0].price) == "40.00"
    assert sorted(s.name for s in everything) == ["Haircut", "Hot towel shave"]


@pytest.fixture
def catalog():
    store = MemoryDirectoryStore()
    tenant = _tenant()
    biz = _id(tenant)
    services = {
        name: store.add_service(biz, name, price=price)
        for name, price in [
            ("Haircut", "40.00"),
            ("Kids cut", "25.00"),
            ("Beard trim", "15.00"),
            ("Colour treatment", "90.00"),
            ("Manicure", "30.00"),
            ("Hair wash & blow-dry", None),
        ]
    }
    store.add_service(biz, "Hot towel shave", price="25.00", active=False)
    # Another tenant's exact-name service must never be found.
    store.add_service(_id(_tenant("Beta Barbers")), "Perm", price="80.00")
    return _directory(store), tenant, services


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Beard trim", "Beard trim"),
        ("  BEARD   TRIM ", "Beard trim"),  # case and whitespace
        ("beard trims", "Beard trim"),  # plural
        ("a beard trim", "Beard trim"),  # filler words
        ("trim", "Beard trim"),  # one word of the name
        ("hair cut", "Haircut"),  # spacing of a compound word
        ("haircut", "Haircut"),  # exact beats "Kids cut"
        ("kids", "Kids cut"),
        ("children's cut", "Kids cut"),  # synonym
        ("color treatment", "Colour treatment"),  # spelling variant
        ("mani", "Manicure"),  # common short form
        ("hiarcut", "Haircut"),  # typo
        ("blow dry", "Hair wash & blow-dry"),  # punctuation in the name
    ],
)
async def test_find_service_matches(catalog, query, expected):
    directory, tenant, _ = catalog
    match = await directory.find_service(tenant, query)
    assert isinstance(match, Found), match
    assert match.service.name == expected


async def test_cut_is_ambiguous_between_haircut_and_kids_cut(catalog):
    directory, tenant, services = catalog

    match = await directory.find_service(tenant, "cut")

    assert isinstance(match, Ambiguous)
    assert {c.id for c in match.candidates} == {services["Haircut"].id, services["Kids cut"].id}


@pytest.mark.parametrize(
    "query", ["perm", "Hot towel shave", "massage", "", "   ", "the", "x" * 300]
)
async def test_find_service_not_found(catalog, query):
    """Unknown, inactive, another tenant's, blank or junk: never a guess."""
    directory, tenant, _ = catalog
    assert isinstance(await directory.find_service(tenant, query), NotFound)


async def test_find_service_by_id_is_active_and_tenant_scoped(catalog):
    directory, tenant, services = catalog
    beard = services["Beard trim"]
    found = await directory.find_service(tenant, str(beard.id))
    assert isinstance(found, Found) and found.service.id == beard.id
    assert isinstance(await directory.find_service(tenant, str(uuid.uuid4())), NotFound)
    assert isinstance(await directory.find_service(_tenant("Beta"), str(beard.id)), NotFound)


async def test_find_service_reads_the_store_every_call(catalog):
    """No cache: a service the owner just deactivated is gone on the next lookup."""
    directory, tenant, services = catalog
    assert isinstance(await directory.find_service(tenant, "manicure"), Found)
    services["Manicure"].active = False
    assert isinstance(await directory.find_service(tenant, "manicure"), NotFound)


# --- hours ----------------------------------------------------------------------


async def test_hours_not_set_is_not_closed():
    directory = _directory(MemoryDirectoryStore())
    tenant = _tenant()
    assert await directory.weekly_hours(tenant) == []
    assert await directory.opening_window(tenant, date(2026, 10, 24)) == HoursNotSet(
        date(2026, 10, 24)
    )


async def test_opening_window_is_local_and_tenant_scoped():
    store = MemoryDirectoryStore()
    tenant, other = _tenant(), _tenant("Beta")
    store.set_week(_id(tenant), WEEK)
    directory = _directory(store)

    saturday = await directory.opening_window(tenant, date(2026, 10, 24))
    monday = await directory.opening_window(tenant, date(2026, 10, 26))

    assert isinstance(saturday, Open)
    assert (saturday.start.time(), saturday.end.time()) == (time(10), time(16))
    assert saturday.start.tzinfo == ZoneInfo(NEW_YORK)
    assert saturday.start.astimezone(UTC) == datetime(2026, 10, 24, 14, tzinfo=UTC)  # EDT
    assert monday == Closed(date(2026, 10, 26))
    assert isinstance(await directory.opening_window(other, date(2026, 10, 24)), HoursNotSet)


async def test_opening_window_is_dst_safe_in_both_directions():
    store = MemoryDirectoryStore()
    tenant = _tenant()
    # Both transition days are Sundays: open overnight-adjacent hours across 02:00.
    store.set_week(_id(tenant), {**WEEK, 6: ("01:00", "04:00")})
    directory = _directory(store)

    spring = await directory.opening_window(tenant, SPRING_FORWARD)
    fall = await directory.opening_window(tenant, FALL_BACK)

    assert isinstance(spring, Open) and isinstance(fall, Open)
    # 01:00 EST -> 04:00 EDT is 2 real hours; 01:00 EDT -> 04:00 EST is 4.
    assert _minutes(spring) == 120
    assert _minutes(fall) == 240
    assert spring.start.astimezone(UTC) == datetime(2026, 3, 8, 6, tzinfo=UTC)
    assert spring.end.astimezone(UTC) == datetime(2026, 3, 8, 8, tzinfo=UTC)
    assert fall.end.astimezone(UTC) == datetime(2026, 11, 1, 9, tzinfo=UTC)


async def test_a_window_opening_inside_the_spring_gap_starts_at_the_real_instant():
    store = MemoryDirectoryStore()
    tenant = _tenant()
    store.set_week(_id(tenant), {**WEEK, 6: ("02:30", "05:00")})  # 02:30 doesn't exist that day

    window = await _directory(store).opening_window(tenant, SPRING_FORWARD)

    assert isinstance(window, Open)
    assert window.start.astimezone(UTC) == datetime(2026, 3, 8, 7, 30, tzinfo=UTC)
    assert window.start.time() == time(3, 30)  # shown as the wall time that exists
    assert _minutes(window) == 90


async def test_same_wall_hours_shift_in_utc_across_the_transition_week():
    store = MemoryDirectoryStore()
    tenant = _tenant()
    store.set_week(_id(tenant), WEEK)
    directory = _directory(store)

    before = await directory.opening_window(tenant, date(2026, 10, 31))  # Sat, EDT
    after = await directory.opening_window(tenant, date(2026, 11, 7))  # Sat, EST

    assert isinstance(before, Open) and isinstance(after, Open)
    assert before.start.time() == after.start.time() == time(10)
    assert before.start.astimezone(UTC).hour == 14 and after.start.astimezone(UTC).hour == 15


async def test_today_is_the_business_local_date():
    tenant = _tenant()
    # 02:30 UTC on Oct 22 is still Oct 21 in New York.
    directory = _directory(MemoryDirectoryStore(), datetime(2026, 10, 22, 2, 30, tzinfo=UTC))
    assert directory.today(tenant) == date(2026, 10, 21)
    tokyo = _tenant("Tokyo Salon", "Asia/Tokyo")
    assert directory.today(tokyo) == date(2026, 10, 22)


async def test_schedule_covers_consecutive_days_in_one_read_and_is_bounded():
    store = MemoryDirectoryStore()
    tenant = _tenant()
    store.set_week(_id(tenant), WEEK)
    directory = _directory(store)

    week = await directory.schedule(tenant, date(2026, 10, 19), 7)  # Monday

    assert store.reads == 1
    assert [w.local_date for w in week] == [date(2026, 10, 19) + timedelta(d) for d in range(7)]
    assert [type(w).__name__ for w in week] == [
        "Closed",
        "Open",
        "Open",
        "Open",
        "Open",
        "Open",
        "Closed",
    ]
    assert len(await directory.schedule(tenant, date(2026, 10, 19), 500)) == 31
    assert len(await directory.schedule(tenant, date(2026, 10, 19), 0)) == 1


async def test_weekly_hours_monday_first():
    store = MemoryDirectoryStore()
    tenant = _tenant()
    store.set_week(_id(tenant), WEEK)
    hours = await _directory(store).weekly_hours(tenant)
    assert [h.day_of_week for h in hours] == list(range(7))
    assert hours[0].is_closed and hours[5].opens_at == time(10)


@pytest.mark.parametrize(
    ("query", "catalog_names", "expected"),
    [
        # Part of a word or fuzzy similarity can't confirm a service: ask, don't price.
        ("men's cut", ["Women's cut"], ["Women's cut"]),
        ("a perm", ["Permanent makeup"], ["Permanent makeup"]),
        ("gel manicure", ["Manicure"], ["Manicure"]),
        ("kids haircut", ["Haircut"], ["Haircut"]),
    ],
)
async def test_a_partial_or_fuzzy_match_is_never_a_confident_one(query, catalog_names, expected):
    store = MemoryDirectoryStore()
    tenant = _tenant()
    for name in catalog_names:
        store.add_service(_id(tenant), name, price="50.00")

    match = await _directory(store).find_service(tenant, query)

    assert isinstance(match, Ambiguous), match
    assert [c.name for c in match.candidates] == expected
