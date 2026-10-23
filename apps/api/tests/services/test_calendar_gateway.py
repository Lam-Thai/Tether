"""`GoogleCalendarGateway` over `FakeGoogleCalendarApi` and the in-memory usage store:
metering, bounded retries, error mapping and token handling, with no database."""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from core.retry import RetryPolicy
from integrations.google_calendar.fake import FakeGoogleCalendarApi, fake_calendar
from integrations.google_calendar.port import EventDraft, GoogleApiError, TimeWindow
from services.calendar import (
    CALENDAR_RETRY,
    CalendarAuthRevokedError,
    CalendarConnection,
    CalendarNotFoundError,
    CalendarNotSelectedError,
    CalendarUnavailableError,
    GoogleCalendarGateway,
    event_id_for,
    token_context,
)
from services.token_vault import SealedToken, TokenVault
from services.usage import DailyLimit, UsageKind
from tests.services.memory_usage_store import MemoryUsageStore, guard, limits

START = datetime(2026, 11, 2, 14, tzinfo=UTC)
WINDOW = TimeWindow(START, START + timedelta(hours=8))
FAST_RETRY = RetryPolicy(attempts=2)
TRANSIENT = GoogleApiError("server_error", retryable=True)

OWNED = fake_calendar("work", "Work", primary=True)
SHARED = fake_calendar("team", "Team", access_role="writer")
READONLY = fake_calendar("holidays", "Holidays", access_role="reader")


class Setup:
    def __init__(self, *, retry: RetryPolicy = FAST_RETRY, usage_limits=None) -> None:
        self.business_id = uuid.uuid4()
        self.api = FakeGoogleCalendarApi()
        self.vault = TokenVault({"k1": os.urandom(32)}, "k1")
        self.store = MemoryUsageStore()
        self.gateway = GoogleCalendarGateway(
            self.api, self.vault, guard(self.store, usage_limits), retry
        )
        self.refresh = ""

    async def connect(self, *, calendar_id: str | None = "work") -> CalendarConnection:
        refresh = self.api.issue_grant(calendars=[OWNED, SHARED, READONLY])
        assert refresh is not None
        await self.api.exchange_code(code="c", code_verifier="v", redirect_uri="r")
        self.api.calls.clear()
        self.refresh = refresh
        sealed = self.vault.encrypt(refresh, context=token_context(self.business_id))
        return CalendarConnection(self.business_id, sealed, calendar_id, "connected")

    def metered(self) -> int:
        return self.store.count(UsageKind.CALENDAR_CALL, self.business_id)

    def failed(self) -> int:
        return self.store.count(UsageKind.FAILED_REQUEST, self.business_id)


async def test_list_calendars_refreshes_once_and_returns_only_owned_calendars():
    s = Setup()
    conn = await s.connect()

    calendars = await s.gateway.list_calendars(conn)

    assert [c.id for c in calendars] == ["work"]
    assert [call[0] for call in s.api.calls] == ["refresh_access_token", "list_calendars"]
    assert s.metered() == 2  # the refresh and the data call are each metered


async def test_free_busy_create_and_cancel_use_the_selected_calendar():
    s = Setup()
    conn = await s.connect()
    s.api.add_busy(s.refresh, "work", START + timedelta(hours=1), START + timedelta(hours=2))

    busy = await s.gateway.free_busy(conn, WINDOW)
    draft = EventDraft("Haircut", START, START + timedelta(hours=1))
    created = await s.gateway.create_event(conn, draft, "booking-1")
    again = await s.gateway.create_event(conn, draft, "booking-1")
    await s.gateway.cancel_event(conn, created.id)

    assert [(b.start, b.end) for b in busy] == [
        (START + timedelta(hours=1), START + timedelta(hours=2))
    ]
    assert created.id == again.id == event_id_for(s.business_id, "booking-1")
    assert s.api.events(s.refresh) == {}
    assert ("insert_event", "work", created.id) in s.api.calls


def test_event_ids_are_google_valid_and_stable():
    biz = uuid.uuid4()
    event_id = event_id_for(biz, "k")
    assert event_id == event_id_for(biz, "k") != event_id_for(biz, "other")
    assert 5 <= len(event_id) <= 1024
    assert set(event_id) <= set("0123456789abcdefghijklmnopqrstuv")
    assert event_id != event_id_for(uuid.uuid4(), "k")


async def test_a_retryable_failure_is_retried_and_every_attempt_is_metered():
    s = Setup()
    conn = await s.connect()
    s.api.fail_next("list_calendars", TRANSIENT)

    calendars = await s.gateway.list_calendars(conn)

    assert [c.id for c in calendars] == ["work"]
    assert len(s.api.calls_to("list_calendars")) == 2
    assert s.metered() == 3  # refresh + failed attempt + retry
    assert s.failed() == 1


async def test_attempts_are_bounded_then_unavailable():
    s = Setup()
    conn = await s.connect()
    s.api.fail_next("list_calendars", TRANSIENT, times=5)

    with pytest.raises(CalendarUnavailableError) as caught:
        await s.gateway.list_calendars(conn)

    assert len(s.api.calls_to("list_calendars")) == 2
    assert str(caught.value) == "server_error"
    assert s.failed() == 2


async def test_the_default_policy_is_two_attempts():
    assert CALENDAR_RETRY.attempts == 2


async def test_auth_revoked_is_not_retried():
    s = Setup()
    conn = await s.connect()
    s.api.fail_next("list_calendars", GoogleApiError("unauthorized", auth_revoked=True))

    with pytest.raises(CalendarAuthRevokedError):
        await s.gateway.list_calendars(conn)

    assert len(s.api.calls_to("list_calendars")) == 1


async def test_a_grant_revoked_at_google_surfaces_on_refresh():
    s = Setup()
    conn = await s.connect()
    s.api.revoke_externally(s.refresh)

    with pytest.raises(CalendarAuthRevokedError) as caught:
        await s.gateway.free_busy(conn, WINDOW)

    assert caught.value.reason == "auth_revoked"
    assert s.api.calls_to("free_busy") == []
    assert len(s.api.calls_to("refresh_access_token")) == 1  # invalid_grant is final


async def test_a_token_that_cannot_be_decrypted_is_treated_as_revoked_without_a_call():
    s = Setup()
    conn = await s.connect()
    foreign = SealedToken("k1", s.vault.encrypt("x", context="calendar:someone-else").ciphertext)
    broken = CalendarConnection(conn.business_id, foreign, conn.calendar_id, conn.status)

    with pytest.raises(CalendarAuthRevokedError) as caught:
        await s.gateway.list_calendars(broken)

    assert caught.value.reason == "token_unreadable"
    assert s.api.calls == []
    assert s.store.events == []


async def test_not_found_and_no_selection():
    s = Setup()
    conn = await s.connect()
    with pytest.raises(CalendarNotFoundError):
        await s.gateway.cancel_event(conn, "missing-event")

    unselected = await s.connect(calendar_id=None)
    with pytest.raises(CalendarNotSelectedError):
        await s.gateway.free_busy(unselected, WINDOW)
    assert s.api.calls == []


async def test_a_reached_limit_makes_zero_provider_calls_and_is_unavailable():
    s = Setup(usage_limits=limits(calendar_call=DailyLimit(0, 0)))
    conn = await s.connect()

    with pytest.raises(CalendarUnavailableError):
        await s.gateway.list_calendars(conn)

    assert s.api.calls == []


async def test_an_unreadable_usage_store_fails_closed_for_a_capped_kind():
    s = Setup(usage_limits=limits(calendar_call=DailyLimit(100, 100)))
    conn = await s.connect()
    s.store.fail_reserve = True

    with pytest.raises(CalendarUnavailableError):
        await s.gateway.list_calendars(conn)

    assert s.api.calls == []


async def test_revoke_reports_whether_google_confirmed():
    s = Setup()
    conn = await s.connect()

    assert await s.gateway.revoke(conn) is True
    assert s.api.revoked == {s.refresh}

    s.api.fail_next("revoke", TRANSIENT, times=5)
    assert await s.gateway.revoke(conn) is False

    foreign = SealedToken("nope", b"x" * 40)
    assert await s.gateway.revoke(CalendarConnection(s.business_id, foreign, None, "x")) is False
