"""`get_services` and `get_business_info` at their boundary, through `ToolExecutor`.

In-memory directory store, pinned clock, no DB, no model.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from core.tenant import Tenant
from models.business import Business
from models.user import User
from schemas.agent_turn import ToolCall
from services.business_tools import (
    GET_BUSINESS_INFO,
    GET_SERVICES,
    MAX_DAYS_AHEAD,
    MAX_DESCRIPTION_CHARS,
    MAX_INFO_DAYS,
    MAX_LISTED_SERVICES,
    get_business_info_tool,
    get_services_tool,
)
from services.directory import BusinessDirectory
from services.tools import ToolContext, ToolExecutor, ToolRegistry
from tests.services.memory_directory_store import MemoryDirectoryStore
from tests.services.memory_faq_store import MemoryOperationLog

NEW_YORK = "America/New_York"
# 15:00 UTC is 11:00 in New York: local date Wednesday 2026-10-21.
NOW = datetime(2026, 10, 21, 15, 0, tzinfo=UTC)
TODAY = date(2026, 10, 21)
WEEK = {
    0: None,  # Monday closed
    1: ("09:00", "18:00"),
    2: ("09:00", "18:00"),
    3: ("09:00", "20:00"),
    4: ("09:00", "18:00"),
    5: ("10:00", "16:00"),  # Saturday
    6: None,  # Sunday closed
}


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


class Setup:
    def __init__(self, now: datetime = NOW, timezone: str = NEW_YORK) -> None:
        self.store = MemoryDirectoryStore()
        self.log = MemoryOperationLog()
        self.tenant = _tenant(timezone=timezone)
        self.directory = BusinessDirectory(self.store, lambda: now)
        registry = ToolRegistry.of(
            [get_services_tool(self.directory), get_business_info_tool(self.directory)]
        )
        self.executor = ToolExecutor(registry, self.log)
        self.ctx = ToolContext(tenant=self.tenant, interaction_id=uuid.uuid4())

    def add(self, name: str, **kwargs):
        return self.store.add_service(_id(self.tenant), name, **kwargs)

    async def call(self, name: str, **arguments):
        return await self.executor.execute(self.ctx, ToolCall(name=name, arguments=arguments))


@pytest.fixture
def salon() -> Setup:
    setup = Setup()
    setup.add("Haircut", duration_minutes=45, price="40.00", description="Cut and style.")
    setup.add("Kids cut", duration_minutes=20, price="18.00")
    setup.add("Beard trim", duration_minutes=15, price="15.00")
    setup.add("Colour consultation", duration_minutes=20, price=None)
    setup.add("Hot towel shave", duration_minutes=30, price="30.00", active=False)
    setup.store.set_week(_id(setup.tenant), WEEK)
    return setup


# --- Declarations ------------------------------------------------------------


def test_declarations_are_flat_optional_and_typed():
    setup = Setup()
    services, info = ToolRegistry.of(
        [get_services_tool(setup.directory), get_business_info_tool(setup.directory)]
    ).declarations()

    assert (services.name, info.name) == (GET_SERVICES, GET_BUSINESS_INFO)
    assert "required" not in services.parameters and "required" not in info.parameters
    query = services.parameters["properties"]["query"]
    assert query["type"] == "string" and query["maxLength"] == 100
    props = info.parameters["properties"]
    assert props["date"]["type"] == "string" and "YYYY-MM-DD" in props["date"]["description"]
    assert props["days"] == {
        "type": "integer",
        "description": props["days"]["description"],
        "minimum": 1,
        "maximum": MAX_INFO_DAYS,
    }


# --- get_services --------------------------------------------------------------


async def test_listing_returns_only_active_services_with_verified_prices(salon):
    outcome = await salon.call(GET_SERVICES)

    assert outcome.status == "succeeded"
    listed = outcome.for_model["services"]
    assert [s["name"] for s in listed] == [
        "Beard trim",
        "Colour consultation",
        "Haircut",
        "Kids cut",
    ]
    assert outcome.for_model["currency"] == "CAD" and outcome.for_model["count"] == 4
    haircut = next(s for s in listed if s["name"] == "Haircut")
    assert haircut["price"] == "40.00" and haircut["duration_minutes"] == 45
    assert haircut["description"] == "Cut and style."
    assert next(s for s in listed if s["name"] == "Colour consultation")["price"] is None
    # Never offered, never priced.
    assert "Hot towel shave" not in str(outcome.for_model)
    assert Decimal("30.00") not in outcome.evidence.prices
    assert set(outcome.evidence.prices) == {Decimal("40"), Decimal("18"), Decimal("15")}
    assert set(outcome.evidence.durations) == {45, 20, 15}
    assert set(outcome.evidence.sources.values()) == {"service"}
    assert set(outcome.evidence.sources) == {s["id"] for s in listed}
    (row,) = salon.log.rows
    assert row["status"] == "succeeded" and row["arguments"] == {"query": None}
    assert row["result"] == {"match": "list", "service_ids": [s["id"] for s in listed], "count": 4}
    assert "Haircut" not in str(row)  # owner text never reaches the operation log


async def test_a_long_catalog_is_capped_and_says_how_many_were_left_out():
    setup = Setup()
    for i in range(MAX_LISTED_SERVICES + 5):
        setup.add(f"Service {i:02d}", description="x" * 500)

    outcome = await setup.call(GET_SERVICES)

    assert len(outcome.for_model["services"]) == MAX_LISTED_SERVICES
    assert outcome.for_model["not_shown"] == 5
    assert all(
        len(s["description"]) == MAX_DESCRIPTION_CHARS for s in outcome.for_model["services"]
    )
    assert len(outcome.evidence.sources) == MAX_LISTED_SERVICES


async def test_an_empty_catalog_is_said_out_loud():
    outcome = await Setup().call(GET_SERVICES)
    assert outcome.for_model == {
        "match": "list",
        "count": 0,
        "result": "the business lists no services",
    }
    assert not outcome.evidence.sources


@pytest.mark.parametrize("query", ["How much is a beard trim?", "BEARD TRIMS", "beard trim"])
async def test_a_named_service_is_found_with_its_price_and_duration(salon, query):
    outcome = await salon.call(GET_SERVICES, query=query)

    assert outcome.for_model["match"] == "found"
    service = outcome.for_model["service"]
    assert (service["name"], service["price"], service["duration_minutes"]) == (
        "Beard trim",
        "15.00",
        15,
    )
    assert outcome.for_model["currency"] == "CAD"
    assert dict(outcome.evidence.sources) == {service["id"]: "service"}
    assert outcome.evidence.prices == (Decimal("15.00"),) and outcome.evidence.durations == (15,)
    assert not outcome.evidence.unresolved
    assert salon.log.rows[0]["result"] == {"match": "found", "service_ids": [service["id"]]}


async def test_cut_is_ambiguous_and_lists_candidates_without_prices(salon):
    outcome = await salon.call(GET_SERVICES, query="cut")

    assert outcome.for_model["match"] == "ambiguous"
    names = {c["name"] for c in outcome.for_model["candidates"]}
    assert names == {"Haircut", "Kids cut"}
    assert "price" not in str(outcome.for_model) and "40.00" not in str(outcome.for_model)
    assert outcome.evidence.prices == () and outcome.evidence.durations == ()
    assert outcome.evidence.unresolved == {"PRICE_INQUIRY"}
    ids = [c["id"] for c in outcome.for_model["candidates"]]
    assert set(outcome.evidence.sources) == set(ids)
    assert salon.log.rows[0]["result"] == {"match": "ambiguous", "service_ids": ids}


async def test_an_unknown_service_is_not_found_and_never_priced(salon):
    outcome = await salon.call(GET_SERVICES, query="How much is a perm?")

    assert outcome.for_model == {
        "match": "not_found",
        "result": "the business does not list this service",
    }
    assert outcome.evidence.unresolved == {"PRICE_INQUIRY"}
    assert not outcome.evidence.sources and not outcome.evidence.prices
    assert salon.log.rows[0]["result"] == {"match": "not_found", "service_ids": []}


async def test_an_inactive_service_is_not_found_by_name_or_by_id(salon):
    shave = next(s for s in salon.store.services if s.name == "Hot towel shave")

    by_name = await salon.call(GET_SERVICES, query="hot towel shave")
    by_id = await salon.call(GET_SERVICES, query=str(shave.id))

    assert by_name.for_model["match"] == by_id.for_model["match"] == "not_found"
    assert "30.00" not in str(by_name) + str(by_id)


async def test_another_tenants_services_are_never_returned(salon):
    other = _tenant("Beta Barbers")
    theirs = salon.store.add_service(_id(other), "Perm", duration_minutes=90, price="80.00")

    listed = await salon.call(GET_SERVICES)
    by_name = await salon.call(GET_SERVICES, query="perm")
    by_id = await salon.call(GET_SERVICES, query=str(theirs.id))

    assert "Perm" not in str(listed.for_model)
    assert by_name.for_model["match"] == by_id.for_model["match"] == "not_found"
    assert "80.00" not in str([listed, by_name, by_id])


async def test_a_blank_query_lists_and_bad_arguments_are_rejected_unread(salon):
    blank = await salon.call(GET_SERVICES, query="   ")
    assert blank.status == "succeeded" and blank.for_model["match"] == "list"

    reads = salon.store.reads
    too_long = await salon.call(GET_SERVICES, query="x" * 101)
    wrong_type = await salon.call(GET_SERVICES, query=42)
    extra = await salon.call(GET_SERVICES, query="cut", tenant_id=str(uuid.uuid4()))

    assert [o.status for o in (too_long, wrong_type, extra)] == ["rejected"] * 3
    assert too_long.for_model == {"error": "invalid_arguments", "fields": ["query"]}
    assert extra.for_model["unexpected_arguments"] == 1
    assert salon.store.reads == reads  # never reached the directory


# --- get_business_info ---------------------------------------------------------


async def test_default_is_the_profile_and_the_next_seven_days_in_local_time(salon):
    outcome = await salon.call(GET_BUSINESS_INFO)

    assert outcome.status == "succeeded"
    result = outcome.for_model
    assert result["business"] == {
        "name": "Alpha Salon",
        "address": "12 Elm St, Springfield",
        "phone": "+1 (312) 847-1928",
        "email": "hello@alpha.test",
        "timezone": NEW_YORK,
        "currency": "CAD",
    }
    assert result["today"] == "2026-10-21" and result["hours_listed"] is True
    days = result["days"]
    assert [d["date"] for d in days] == [(TODAY + timedelta(n)).isoformat() for n in range(7)]
    assert days[0] == {
        "date": "2026-10-21",
        "weekday": "Wednesday",
        "status": "open",
        "opens": "09:00",
        "closes": "18:00",
    }
    saturday = next(d for d in days if d["weekday"] == "Saturday")
    assert (saturday["opens"], saturday["closes"]) == ("10:00", "16:00")
    assert next(d for d in days if d["weekday"] == "Monday") == {
        "date": "2026-10-26",
        "weekday": "Monday",
        "status": "closed",
    }
    assert dict(outcome.evidence.sources) == {
        "business_profile": "business_profile",
        "business_hours": "business_hours",
    }
    (row,) = salon.log.rows
    assert row["arguments"] == {"date": None, "days": None}
    assert row["result"] == {"start": "2026-10-21", "days": 7, "open": 5, "closed": 2, "not_set": 0}
    assert "Elm" not in str(row)


async def test_a_given_date_reports_that_day_or_the_days_asked_for(salon):
    one = await salon.call(GET_BUSINESS_INFO, date="2026-12-05")
    three = await salon.call(GET_BUSINESS_INFO, date="2026-12-05", days=3)

    assert [d["date"] for d in one.for_model["days"]] == ["2026-12-05"]
    assert one.for_model["days"][0]["weekday"] == "Saturday"
    assert [d["status"] for d in three.for_model["days"]] == ["open", "closed", "closed"]


@pytest.mark.parametrize(
    ("given", "days", "expected"),
    [
        (TODAY, None, 1),
        (TODAY + timedelta(MAX_DAYS_AHEAD), None, 1),
        (TODAY + timedelta(MAX_DAYS_AHEAD - 1), 5, 2),  # clamped at the horizon
    ],
)
async def test_dates_up_to_a_year_ahead_are_answered(salon, given, days, expected):
    args = {"date": given.isoformat(), **({"days": days} if days else {})}
    outcome = await salon.call(GET_BUSINESS_INFO, **args)
    assert outcome.status == "succeeded" and len(outcome.for_model["days"]) == expected


@pytest.mark.parametrize(
    "given", [TODAY - timedelta(1), TODAY + timedelta(MAX_DAYS_AHEAD + 1), date(2030, 1, 1)]
)
async def test_dates_outside_the_window_are_rejected_before_any_hours_read(salon, given):
    reads = salon.store.reads

    outcome = await salon.call(GET_BUSINESS_INFO, date=given.isoformat())

    assert outcome.status == "rejected"
    assert outcome.for_model == {
        "error": "date_out_of_range",
        "fields": ["date"],
        "earliest": "2026-10-21",
        "latest": (TODAY + timedelta(MAX_DAYS_AHEAD)).isoformat(),
    }
    assert salon.store.reads == reads
    (row,) = salon.log.rows
    assert row["status"] == "rejected" and row["arguments"] == {"argument_names": ["date"]}
    assert row["result"]["reason"] == "date_out_of_range"


@pytest.mark.parametrize(
    "arguments",
    [
        {"date": "next saturday"},
        {"date": "2026-02-30"},
        {"date": "2026-10-24T10:00"},
        {"date": 1793318400},
        {"days": 0},
        {"days": MAX_INFO_DAYS + 1},
        {"days": "3"},
        {"days": 2.5},
    ],
)
async def test_malformed_dates_and_day_counts_are_rejected(salon, arguments):
    outcome = await salon.call(GET_BUSINESS_INFO, **arguments)
    assert outcome.status == "rejected"
    assert outcome.for_model["fields"] == sorted(arguments)


async def test_hours_not_set_are_not_set_never_closed():
    setup = Setup()  # the owner never saved hours

    outcome = await setup.call(GET_BUSINESS_INFO)

    result = outcome.for_model
    assert result["hours_listed"] is False
    assert result["result"] == "the business has not listed its opening hours"
    assert {d["status"] for d in result["days"]} == {"not_set"}
    assert "closed" not in str(result["days"])
    assert dict(outcome.evidence.sources) == {"business_profile": "business_profile"}


async def test_windows_are_local_wall_times_across_both_dst_transitions():
    # Clocks fall back on Sunday 2026-11-01: the wall-clock hours don't move.
    setup = Setup()
    setup.store.set_week(_id(setup.tenant), {**WEEK, 6: ("09:00", "17:00")})

    outcome = await setup.call(GET_BUSINESS_INFO, date="2026-10-30", days=5)

    by_date = {d["date"]: d for d in outcome.for_model["days"]}
    assert (by_date["2026-10-31"]["opens"], by_date["2026-10-31"]["closes"]) == ("10:00", "16:00")
    assert (by_date["2026-11-01"]["opens"], by_date["2026-11-01"]["closes"]) == ("09:00", "17:00")
    assert (by_date["2026-11-03"]["opens"], by_date["2026-11-03"]["closes"]) == ("09:00", "18:00")


async def test_an_opening_time_inside_the_spring_forward_gap_moves_to_the_real_one():
    # Clocks jump 02:00 -> 03:00 on Sunday 2026-03-08: 02:30 doesn't exist that day.
    setup = Setup(now=datetime(2026, 3, 1, 15, 0, tzinfo=UTC))
    setup.store.set_week(_id(setup.tenant), {**WEEK, 6: ("02:30", "05:00")})

    outcome = await setup.call(GET_BUSINESS_INFO, date="2026-03-08", days=8)

    days = outcome.for_model["days"]
    assert (days[0]["opens"], days[0]["closes"]) == ("03:30", "05:00")
    assert (days[7]["date"], days[7]["opens"]) == ("2026-03-15", "02:30")  # a normal Sunday


async def test_today_is_the_businesss_local_date_not_utcs():
    # 02:00 UTC on the 22nd is still the 21st in New York.
    setup = Setup(now=datetime(2026, 10, 22, 2, 0, tzinfo=UTC))
    setup.store.set_week(_id(setup.tenant), WEEK)

    outcome = await setup.call(GET_BUSINESS_INFO, date="2026-10-21")

    assert outcome.status == "succeeded" and outcome.for_model["today"] == "2026-10-21"


async def test_hours_are_scoped_to_the_tenant():
    setup = Setup()
    other = _tenant("Beta Barbers")
    setup.store.set_week(_id(other), WEEK)

    outcome = await setup.call(GET_BUSINESS_INFO)

    assert outcome.for_model["hours_listed"] is False
    assert {d["status"] for d in outcome.for_model["days"]} == {"not_set"}
