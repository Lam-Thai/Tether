"""`DashboardMetrics` on real Postgres: a seeded dataset whose counts are asserted exactly,
range edges in a non-UTC business timezone (and across a DST change), the default range,
the rules that bound a range, and rows of another business that must not count."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from core.db import async_session_factory
from core.readiness import SqlMetricsQueries
from core.tenant import Tenant
from models.booking import Booking
from models.business import Business
from models.escalation import Escalation
from models.user import User
from services.dashboard import DashboardMetrics, DateRange
from services.inbox import InvalidDateRangeError
from tests.api.inbox_factory import add_interaction

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)  # 08:00 on Oct 28 in New York


def _tenant(business_id: uuid.UUID, timezone: str) -> Tenant:
    user = User(id=uuid.uuid4(), external_auth_id="user_x", email="owner@example.com")
    business = Business(
        id=business_id,
        owner_id=user.id,
        name="Alpha Salon",
        industry="Salon",
        address="1 Main St",
        timezone=timezone,
    )
    return Tenant(user=user, business=business)


async def _summary(business_id, range_, timezone="America/New_York"):
    async with async_session_factory() as session:
        metrics = DashboardMetrics(SqlMetricsQueries(session), lambda: NOW)
        start, end = (range_.start, range_.end) if range_ else (None, None)
        return await metrics.summary(_tenant(business_id, timezone), start, end)


async def _add_booking(
    business_id: uuid.UUID, created_at: datetime, status: str, hour: int, day: int = 1
) -> None:
    start = datetime(2031, 5, day, hour, 0, tzinfo=UTC)
    async with async_session_factory() as session:
        session.add(
            Booking(
                id=uuid.uuid4(),
                business_id=business_id,
                service_name="Haircut",
                timezone="America/New_York",
                calendar_event_id="evt" if status == "confirmed" else None,
                customer_name="Alex Synthetic",
                start_time=start,
                end_time=start + timedelta(minutes=45),
                blocked_until=start + timedelta(minutes=45),
                status=status,
                idempotency_key=uuid.uuid4().hex,
                created_at=created_at,
            )
        )
        await session.commit()


async def _add_escalation(business_id, interaction_id, status: str, reason="urgent") -> None:
    async with async_session_factory() as session:
        session.add(
            Escalation(
                id=uuid.uuid4(),
                business_id=business_id,
                interaction_id=interaction_id,
                reason=reason,
                urgency="normal",
                status=status,
                sms_status="disabled",
            )
        )
        await session.commit()


def at(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)  # type: ignore[arg-type]


async def _seed(business_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """Business-local range 2026-09-29..2026-10-28 in New York (EDT, UTC-4) is
    [2026-09-29T04:00Z, 2026-10-29T04:00Z)."""
    ids: dict[str, uuid.UUID] = {}

    async def add(name: str, started_at: datetime, intent: str | None, outcome, **kw) -> None:
        status = "in_progress" if outcome is None else "completed"
        ids[name] = await add_interaction(
            business_id, started_at=started_at, intent=intent, outcome=outcome, status=status, **kw
        )

    await add("faq_chat", at(2026, 10, 20, 15), "FAQ", "answered")
    await add("faq_call", at(2026, 10, 21, 15), "FAQ", "answered", channel="voice")
    await add("hours", at(2026, 10, 22, 15), "BUSINESS_HOURS", "answered")
    await add("price_escalated", at(2026, 10, 23, 15), "PRICE_INQUIRY", "escalated")
    await add("faq_open", at(2026, 10, 24, 15), "FAQ", None)  # in progress: not answered yet
    await add("booked", at(2026, 10, 25, 15), "BOOK_APPOINTMENT", "booked")
    await add("no_intent", at(2026, 10, 26, 15), None, "answered")
    # Range edges (Oct 28 23:59:59 local is the last second; Oct 29 00:00 local is out).
    await add("last_second_in", at(2026, 10, 29, 3, 59, 59), "LOCATION", "answered")
    await add("first_after_out", at(2026, 10, 29, 4, 0, 0), "FAQ", "answered")
    await add("first_instant_in", at(2026, 9, 29, 4, 0, 0), "FAQ", "answered")
    await add("last_before_out", at(2026, 9, 29, 3, 59, 59), "FAQ", "answered")
    return ids


async def test_the_default_range_counts_exactly_what_the_business_recorded(two_tenants):
    tenant_a, tenant_b = two_tenants
    ids = await _seed(tenant_a.business_id)
    ids_b = await _seed(tenant_b.business_id)  # the same dataset for another business
    # Bookings are counted by when they were created: two confirmed in range (one on the
    # first instant), one just before the range, and three that are not confirmed.
    await _add_booking(tenant_a.business_id, at(2026, 10, 10, 15), "confirmed", 15, 1)
    await _add_booking(tenant_a.business_id, at(2026, 9, 29, 4, 0, 0), "confirmed", 16, 1)
    await _add_booking(tenant_a.business_id, at(2026, 9, 29, 3, 59, 59), "confirmed", 17, 1)
    await _add_booking(tenant_a.business_id, at(2026, 10, 11, 15), "cancelled", 18, 1)
    await _add_booking(tenant_a.business_id, at(2026, 10, 12, 15), "failed", 19, 1)
    await _add_booking(tenant_a.business_id, at(2026, 10, 13, 15), "needs_reconciliation", 20, 1)
    await _add_booking(tenant_b.business_id, at(2026, 10, 10, 15), "confirmed", 15, 1)
    # Open escalations are the current queue, wherever their interaction falls in time.
    await _add_escalation(tenant_a.business_id, ids["price_escalated"], "open")
    await _add_escalation(tenant_a.business_id, ids["last_before_out"], "open", "human_requested")
    await _add_escalation(tenant_a.business_id, ids["faq_chat"], "resolved")
    await _add_escalation(tenant_b.business_id, ids_b["booked"], "open")

    result = await _summary(tenant_a.business_id, None)

    assert result.range == DateRange(date(2026, 9, 29), date(2026, 10, 28))
    assert result.timezone == "America/New_York"
    # faq_chat, faq_call, hours, price_escalated, faq_open, booked, no_intent,
    # last_second_in, first_instant_in
    assert result.total_interactions == 9
    # faq_chat, faq_call, hours, last_second_in, first_instant_in (not the escalated price
    # question, the open chat, the booking or the two outside the range)
    assert result.faqs_answered == 5
    assert result.successful_bookings == 2
    assert result.open_escalations == 2


async def test_an_explicit_single_day_range(two_tenants):
    tenant_a, _ = two_tenants
    await _seed(tenant_a.business_id)

    one_day = await _summary(
        tenant_a.business_id, DateRange(date(2026, 10, 20), date(2026, 10, 20))
    )
    quiet = await _summary(tenant_a.business_id, DateRange(date(2026, 10, 1), date(2026, 10, 19)))

    assert (one_day.total_interactions, one_day.faqs_answered) == (1, 1)
    assert (quiet.total_interactions, quiet.faqs_answered) == (0, 0)


async def test_range_edges_follow_the_business_timezone_not_utc(two_tenants):
    tenant_a, _ = two_tenants
    # Oct 28 23:30 in New York is already Oct 29 03:30 UTC; Oct 29 00:30 local is 04:30 UTC.
    await add_interaction(tenant_a.business_id, started_at=at(2026, 10, 29, 3, 30))
    await add_interaction(tenant_a.business_id, started_at=at(2026, 10, 29, 4, 30))
    # And in Auckland (UTC+13 in October) the same instants are Oct 29 16:30 / 17:30 local.
    oct28 = DateRange(date(2026, 10, 28), date(2026, 10, 28))
    oct29 = DateRange(date(2026, 10, 29), date(2026, 10, 29))

    new_york_28 = await _summary(tenant_a.business_id, oct28)
    new_york_29 = await _summary(tenant_a.business_id, oct29)
    utc_28 = await _summary(tenant_a.business_id, oct28, "UTC")
    auckland_28 = await _summary(tenant_a.business_id, oct28, "Pacific/Auckland")
    auckland_29 = await _summary(tenant_a.business_id, oct29, "Pacific/Auckland")

    assert new_york_28.total_interactions == 1
    assert new_york_29.total_interactions == 1
    assert utc_28.total_interactions == 0  # both are Oct 29 in UTC
    assert auckland_28.total_interactions == 0
    assert auckland_29.total_interactions == 2


async def test_a_range_across_a_dst_change_uses_each_days_own_offset(two_tenants):
    tenant_a, _ = two_tenants
    # New York leaves daylight time on 2026-11-01 (that day is 25 hours long).
    # Oct 31 00:00 EDT = 04:00Z; Nov 3 00:00 EST = 05:00Z.
    for started in (
        at(2026, 10, 31, 3, 59, 59),  # Oct 30 23:59:59 local: out
        at(2026, 10, 31, 4, 0, 0),  # first instant: in
        at(2026, 11, 3, 4, 59, 59),  # Nov 2 23:59:59 EST: in (the offset changed to UTC-5)
        at(2026, 11, 3, 5, 0, 0),  # Nov 3 00:00 EST: out
    ):
        await add_interaction(tenant_a.business_id, started_at=started)

    result = await _summary(tenant_a.business_id, DateRange(date(2026, 10, 31), date(2026, 11, 2)))

    assert result.total_interactions == 2


async def test_only_the_tenants_business_is_counted(two_tenants):
    tenant_a, tenant_b = two_tenants
    await add_interaction(tenant_b.business_id, started_at=at(2026, 10, 20, 15))
    await _add_booking(tenant_b.business_id, at(2026, 10, 20, 15), "confirmed", 15)
    other = await add_interaction(tenant_b.business_id, started_at=at(2026, 10, 21, 15))
    await _add_escalation(tenant_b.business_id, other, "open")

    mine = await _summary(tenant_a.business_id, None)
    theirs = await _summary(tenant_b.business_id, None)

    assert (mine.total_interactions, mine.successful_bookings, mine.open_escalations) == (0, 0, 0)
    assert (theirs.total_interactions, theirs.successful_bookings) == (2, 1)
    assert theirs.open_escalations == 1


async def test_the_default_range_is_the_last_30_days_ending_today_in_the_business_zone(
    two_tenants,
):
    tenant_a, _ = two_tenants

    # 2026-10-28T12:00Z is Oct 28 in New York and Oct 29 in Auckland.
    new_york = await _summary(tenant_a.business_id, None)
    auckland = await _summary(tenant_a.business_id, None, "Pacific/Auckland")

    assert new_york.range == DateRange(date(2026, 9, 29), date(2026, 10, 28))
    assert auckland.range == DateRange(date(2026, 9, 30), date(2026, 10, 29))
    assert (new_york.range.end - new_york.range.start).days + 1 == 30


async def test_a_partly_given_range_fills_in_the_other_side(two_tenants):
    tenant_a, _ = two_tenants
    async with async_session_factory() as session:
        metrics = DashboardMetrics(SqlMetricsQueries(session), lambda: NOW)
        tenant = _tenant(tenant_a.business_id, "America/New_York")
        only_from = await metrics.summary(tenant, date(2026, 10, 1), None)
        only_to = await metrics.summary(tenant, None, date(2026, 10, 15))

    assert only_from.range == DateRange(date(2026, 10, 1), date(2026, 10, 28))
    assert only_to.range == DateRange(date(2026, 9, 16), date(2026, 10, 15))


@pytest.mark.parametrize(
    ("start", "end"),
    [
        pytest.param(date(2026, 10, 2), date(2026, 10, 1), id="from-after-to"),
        pytest.param(date(2025, 10, 1), date(2026, 10, 2), id="367-days"),
        pytest.param(date(2000, 1, 1), date(2026, 10, 1), id="decades"),
    ],
)
async def test_a_bad_range_is_422_invalid_date_range(two_tenants, start, end):
    tenant_a, _ = two_tenants

    with pytest.raises(InvalidDateRangeError) as raised:
        await _summary(tenant_a.business_id, DateRange(start, end))

    assert raised.value.status_code == 422 and raised.value.code == "invalid_date_range"


async def test_366_days_is_the_longest_allowed_range(two_tenants):
    tenant_a, _ = two_tenants

    result = await _summary(tenant_a.business_id, DateRange(date(2025, 10, 2), date(2026, 10, 2)))

    assert result.total_interactions == 0  # 366 days inclusive: accepted


async def test_the_counts_are_one_round_trip(two_tenants):
    tenant_a, _ = two_tenants
    statements: list[str] = []
    from sqlalchemy import event

    from core.db import engine

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        await _summary(tenant_a.business_id, None)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)

    selects = [s for s in statements if "FROM interactions" in s or "FROM bookings" in s]
    assert len(selects) == 1
