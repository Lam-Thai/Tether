"""`Inbox` and its pure helpers: cursors, business-day windows, operation labels.

No DB: the queries, lifecycle and summarizer are scripted fakes.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from core.tenant import Tenant
from models.business import Business
from models.user import User
from services.inbox import (
    Cursor,
    Inbox,
    InboxFilters,
    InteractionDetail,
    InteractionRow,
    InvalidCursorError,
    InvalidDateRangeError,
    MessageItem,
    MessagePage,
    OperationItem,
    Page,
    day_window,
    decode_cursor,
    encode_cursor,
    operation_label,
)
from services.lifecycle import InteractionNotFoundError


def _tenant(timezone: str = "America/New_York") -> Tenant:
    user = User(id=uuid.uuid4(), external_auth_id="user_x", email="owner@example.com")
    business = Business(
        id=uuid.uuid4(),
        owner_id=user.id,
        name="Alpha Salon",
        industry="Salon",
        address="1 Main St",
        timezone=timezone,
    )
    return Tenant(user=user, business=business)


def _bid(tenant: Tenant) -> uuid.UUID:
    assert tenant.business is not None
    return tenant.business.id


def _row(**overrides) -> InteractionRow:
    values: dict[str, Any] = dict(
        id=uuid.uuid4(),
        channel="test_chat",
        started_at=datetime(2026, 10, 20, 15, tzinfo=UTC),
        ended_at=datetime(2026, 10, 20, 15, 5, tzinfo=UTC),
        caller=None,
        intent="FAQ",
        summary=None,
        summary_source=None,
        status="completed",
        outcome="answered",
        requires_human=False,
    )
    values.update(overrides)
    return InteractionRow(**values)


class FakeQueries:
    def __init__(self) -> None:
        self.rows: list[InteractionRow] = []
        self.details: dict[uuid.UUID, InteractionDetail] = {}
        self.pages: dict[uuid.UUID, MessagePage] = {}
        self.seen: list[tuple] = []
        self.events: list[str] = []

    async def list(self, business_id, filters, cursor, limit):
        self.events.append("list")
        self.seen.append((business_id, filters, cursor, limit))
        return Page(tuple(self.rows), Cursor(datetime(2026, 1, 1, tzinfo=UTC), uuid.uuid4()))

    async def detail(self, business_id, interaction_id):
        self.events.append("detail")
        return self.details.get(interaction_id)

    async def messages(self, business_id, interaction_id, after, limit):
        self.seen.append((business_id, interaction_id, after, limit))
        return self.pages.get(interaction_id)


class FakeLifecycle:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.swept: list[uuid.UUID] = []

    async def sweep_idle(self, business_id):
        self.events.append("sweep")
        self.swept.append(business_id)
        return 0


class FakeSummarizer:
    def __init__(self, queries: FakeQueries, fail: bool = False) -> None:
        self.queries = queries
        self.calls: list[tuple[uuid.UUID, uuid.UUID]] = []
        self.fail = fail

    async def summarize(self, business_id, interaction_id, *, ai=True):
        self.calls.append((business_id, interaction_id))
        if self.fail:
            raise RuntimeError("boom")
        found = self.queries.details[interaction_id]
        self.queries.details[interaction_id] = InteractionDetail(
            _row_like(found.row, summary="Wanted a cut. A cut was booked.", summary_source="ai"),
            found.message_count,
            found.operations,
        )
        return None


def _row_like(row: InteractionRow, **overrides) -> InteractionRow:
    from dataclasses import replace

    return replace(row, **overrides)


def _inbox(fail: bool = False):
    queries = FakeQueries()
    lifecycle = FakeLifecycle(queries.events)
    summarizer = FakeSummarizer(queries, fail)
    return Inbox(queries, lifecycle, summarizer), queries, lifecycle, summarizer  # type: ignore[arg-type]


# --- cursors ----------------------------------------------------------------------


def test_cursor_round_trips_and_is_opaque():
    cursor = Cursor(datetime(2026, 10, 20, 15, 30, 1, 123456, tzinfo=UTC), uuid.uuid4())
    token = encode_cursor(cursor)
    assert decode_cursor(token) == cursor
    assert "=" not in token and "2026" not in token


@pytest.mark.parametrize(
    "token",
    ["", "abc", "!!!!", "bm90LWEtY3Vyc29y", "MjAyNi0xMC0yMHxub3QtYS11dWlk"],
    ids=["empty", "short-garbage", "not-base64", "no-separator", "bad-uuid"],
)
def test_malformed_cursors_are_invalid(token: str):
    with pytest.raises(InvalidCursorError) as caught:
        decode_cursor(token)
    assert caught.value.status_code == 400 and caught.value.code == "invalid_cursor"


def test_a_cursor_without_a_timezone_is_invalid():
    import base64

    token = base64.urlsafe_b64encode(f"2026-10-20T15:00:00|{uuid.uuid4()}".encode()).decode()
    with pytest.raises(InvalidCursorError):
        decode_cursor(token)


# --- business-day windows ----------------------------------------------------------


def test_day_window_is_inclusive_in_the_business_timezone():
    starts_at, ends_before = day_window(date(2026, 10, 20), date(2026, 10, 20), "America/New_York")
    assert starts_at == datetime(2026, 10, 20, 4, tzinfo=UTC)  # 00:00 EDT
    assert ends_before == datetime(2026, 10, 21, 4, tzinfo=UTC)


def test_day_window_across_the_fall_back_day_is_25_hours():
    starts_at, ends_before = day_window(date(2026, 11, 1), date(2026, 11, 1), "America/New_York")
    assert starts_at is not None and ends_before is not None
    assert ends_before - starts_at == timedelta(hours=25)


def test_day_window_open_ended_and_unknown_zone():
    assert day_window(None, None, "America/New_York") == (None, None)
    starts_at, _ = day_window(date(2026, 10, 20), None, "Not/AZone")
    assert starts_at == datetime(2026, 10, 20, tzinfo=UTC)


# --- operation labels --------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "status", "result", "expected"),
    [
        (
            "create_booking",
            "succeeded",
            {"result": "confirmed", "booking_id": "1a2b3c4d-1111-2222-3333-444455556666"},
            "booking 1a2b3c4d",
        ),
        (
            "create_booking",
            "succeeded",
            {"result": "confirmed", "booking_id": "1a2b3c4d-1", "event_id": "ev_9"},
            "booking 1a2b3c4d event ev_9",
        ),
        (
            "create_booking",
            "succeeded",
            {"result": "booking_failed", "reason": "calendar_unavailable"},
            "calendar_unavailable",
        ),
        ("create_booking", "succeeded", {"result": "slot_taken"}, "slot_taken"),
        ("check_availability", "succeeded", {"result": "available", "offered": 3}, "3 slots"),
        (
            "check_availability",
            "succeeded",
            {"result": "no_availability", "reason": "fully_booked"},
            "fully_booked",
        ),
        ("search_faq", "succeeded", {"faq_ids": ["x"], "count": 2}, "2 results"),
        ("search_faq", "succeeded", {"faq_ids": [], "count": 0}, "0 results"),
        (
            "create_booking",
            "rejected",
            {"reason": "invalid_arguments", "fields": ["x"]},
            "invalid_arguments",
        ),
        ("lookup", "failed", {"reason": "executor_error"}, "executor_error"),
        ("lookup", "rejected", {}, "rejected"),
        ("get_services", "succeeded", {"count": 4}, None),
        ("x", "succeeded", {"reason": "free text with spaces"}, None),
    ],
    ids=[
        "booking",
        "booking-with-event",
        "booking-failed",
        "slot-taken",
        "slots",
        "no-availability",
        "results",
        "zero-results",
        "rejected-reason",
        "failed-reason",
        "rejected-status",
        "unlabelled",
        "free-text-never-passes",
    ],
)
def test_operation_label(tool, status, result, expected):
    assert operation_label(tool, status, result) == expected


# --- the facade --------------------------------------------------------------------


async def test_list_sweeps_idle_first_then_queries_with_utc_filters_and_returns_the_timezone():
    inbox, queries, lifecycle, _ = _inbox()
    tenant = _tenant()
    queries.rows = [_row()]
    filters = InboxFilters(
        type="bookings", from_date=date(2026, 10, 20), to_date=date(2026, 10, 21)
    )

    page = await inbox.list(tenant, filters, None, 10)

    assert queries.events == ["sweep", "list"]
    assert lifecycle.swept == [_bid(tenant)]
    business_id, seen, cursor, limit = queries.seen[0]
    assert business_id == _bid(tenant) and cursor is None and limit == 10
    assert seen.type == "bookings"
    assert seen.starts_at == datetime(2026, 10, 20, 4, tzinfo=UTC)
    assert seen.ends_before == datetime(2026, 10, 22, 4, tzinfo=UTC)
    assert page.timezone == "America/New_York" and len(page.items) == 1
    assert decode_cursor(page.next_cursor or "") is not None


async def test_list_rejects_an_inverted_range_before_touching_anything():
    inbox, queries, _, _ = _inbox()
    with pytest.raises(InvalidDateRangeError) as caught:
        await inbox.list(
            _tenant(), InboxFilters(from_date=date(2026, 10, 21), to_date=date(2026, 10, 20)), None
        )
    assert caught.value.status_code == 422 and queries.events == []


async def test_list_rejects_a_bad_cursor_before_touching_anything():
    inbox, queries, _, _ = _inbox()
    with pytest.raises(InvalidCursorError):
        await inbox.list(_tenant(), InboxFilters(), "garbage!", 10)
    assert queries.events == []


async def test_list_clamps_the_limit():
    inbox, queries, _, _ = _inbox()
    await inbox.list(_tenant(), InboxFilters(), None, 10_000)
    await inbox.list(_tenant(), InboxFilters(), None, 0)
    assert [call[3] for call in queries.seen] == [100, 1]


async def test_list_redacts_stored_caller_and_summary_text():
    inbox, queries, _, _ = _inbox()
    queries.rows = [_row(summary="Paid with 4111 1111 1111 1111.", caller="Sam")]

    page = await inbox.list(_tenant(), InboxFilters(), None, 10)

    assert "4111" not in (page.items[0].summary or "")
    assert page.items[0].caller == "Sam"


async def test_detail_summarizes_a_closed_interaction_whose_summary_is_missing():
    inbox, queries, _, summarizer = _inbox()
    tenant = _tenant()
    row = _row()
    queries.details[row.id] = InteractionDetail(row, 3, ())

    view = await inbox.detail(tenant, row.id)

    assert summarizer.calls == [(_bid(tenant), row.id)]
    assert view.detail.row.summary == "Wanted a cut. A cut was booked."
    assert view.detail.row.summary_source == "ai"
    assert view.timezone == "America/New_York"


@pytest.mark.parametrize(
    "row",
    [
        _row(summary="Already there.", summary_source="template"),
        _row(status="in_progress", outcome=None, ended_at=None),
    ],
    ids=["has-summary", "still-in-progress"],
)
async def test_detail_does_not_summarize_when_not_needed(row):
    inbox, queries, _, summarizer = _inbox()
    queries.details[row.id] = InteractionDetail(row, 3, ())

    await inbox.detail(_tenant(), row.id)

    assert summarizer.calls == []


async def test_detail_survives_a_failing_summarizer():
    inbox, queries, _, summarizer = _inbox(fail=True)
    row = _row()
    queries.details[row.id] = InteractionDetail(row, 3, ())

    view = await inbox.detail(_tenant(), row.id)

    assert view.detail.row.summary is None and len(summarizer.calls) == 1


async def test_detail_labels_the_operations():
    inbox, queries, _, _ = _inbox()
    row = _row(summary="Done. Done.", summary_source="template")
    op = OperationItem(
        uuid.uuid4(),
        "check_availability",
        "succeeded",
        datetime(2026, 10, 20, tzinfo=UTC),
        12,
        {"result": "available", "offered": 2},
    )
    queries.details[row.id] = InteractionDetail(row, 3, (op,))

    view = await inbox.detail(_tenant(), row.id)

    assert [o.label for o in view.detail.operations] == ["2 slots"]


async def test_unknown_interaction_is_not_found_for_detail_and_messages():
    inbox, _, _, _ = _inbox()
    with pytest.raises(InteractionNotFoundError):
        await inbox.detail(_tenant(), uuid.uuid4())
    with pytest.raises(InteractionNotFoundError):
        await inbox.messages(_tenant(), uuid.uuid4())


async def test_messages_are_redacted_on_read_and_the_window_is_clamped():
    inbox, queries, _, _ = _inbox()
    interaction_id = uuid.uuid4()
    queries.pages[interaction_id] = MessagePage(
        (
            MessageItem(
                1, "customer", "my card 4111111111111111 cvv 123", datetime.now(UTC), False
            ),
            MessageItem(2, "agent", "Call 555-123-4567", datetime.now(UTC), True),
        ),
        2,
    )

    page = await inbox.messages(_tenant(), interaction_id, after=-5, limit=9999)

    assert "4111" not in page.items[0].text and "123" not in page.items[0].text
    assert page.items[1].text == "Call 555-123-4567" and page.items[1].fallback is True
    assert page.next_after == 2
    assert queries.seen[0][2:] == (0, 200)


async def test_an_out_of_range_date_is_a_422_not_a_crash():
    inbox, queries, _, _ = _inbox()
    with pytest.raises(InvalidDateRangeError):
        await inbox.list(_tenant(), InboxFilters(to_date=date(9999, 12, 31)), None)
    with pytest.raises(InvalidDateRangeError):
        await inbox.list(_tenant("Asia/Tokyo"), InboxFilters(from_date=date(1, 1, 1)), None)
    assert queries.events == []


def test_an_overflowing_cursor_time_is_invalid():
    import base64

    raw = f"0001-01-01T00:00:00+05:00|{uuid.uuid4()}"
    with pytest.raises(InvalidCursorError):
        decode_cursor(base64.urlsafe_b64encode(raw.encode()).decode())
