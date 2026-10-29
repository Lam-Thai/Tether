"""`DELETE /api/v1/business/test-data` over HTTP on real Postgres with the fake Google
Calendar: the confirmation phrase, the calendar events being cancelled first (cancelled,
already gone, revoked, failing), what is deleted and what is kept, and other businesses."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from core.db import async_session_factory
from integrations.google_calendar.port import CreatedEvent, GoogleApiError
from models.agent import Agent
from models.booking import Booking
from models.calendar import CalendarConnection
from models.interaction import Interaction, InteractionMessage
from models.service import Service
from models.usage import UsageCounter, UsageEvent
from tests.api.inbox_factory import add_interaction
from tests.api.test_bookings import OWNED, _auth, _connect, _salon, client  # noqa: F401

# The `client` fixture is imported by name, which pytest needs and ruff reads as a
# redefinition.
# ruff: noqa: F811

URL = "/api/v1/business/test-data"
PHRASE = {"confirmation": "DELETE TEST DATA"}
START = datetime(2031, 8, 1, 9, tzinfo=UTC)


async def _delete(client, tenant, body=PHRASE):
    return await client.request("DELETE", URL, json=body, headers=_auth(tenant))


async def _booking(
    business_id: uuid.UUID, hours: int, event: str | None, status: str = "confirmed"
) -> None:
    start = START + timedelta(hours=hours)
    async with async_session_factory() as session:
        session.add(
            Booking(
                id=uuid.uuid4(),
                business_id=business_id,
                service_name="Haircut",
                timezone="UTC",
                calendar_event_id=event,
                customer_name="Alex Synthetic",
                start_time=start,
                end_time=start + timedelta(minutes=45),
                blocked_until=start + timedelta(minutes=45),
                status=status,
                idempotency_key=uuid.uuid4().hex,
                created_at=start,
            )
        )
        await session.commit()


def _put_event(calendar, refresh: str, event_id: str) -> None:
    calendar.api.events(refresh)[(OWNED.id, event_id)] = CreatedEvent(
        id=event_id, html_link=None, start=START, end=START + timedelta(minutes=45)
    )


async def _count(model, business_id: uuid.UUID) -> int:
    async with async_session_factory() as session:
        return int(
            await session.scalar(
                select(func.count()).select_from(model).where(model.business_id == business_id)
            )
            or 0
        )


async def _seed_chat(business_id: uuid.UUID) -> None:
    await add_interaction(business_id)
    await add_interaction(business_id, channel="voice")


async def test_it_cancels_the_events_deletes_the_test_data_and_keeps_the_configuration(
    client, entitled_tenants, calendar, usage
):
    tenant, other = entitled_tenants
    business_id = tenant.business_id
    refresh = await _connect(client, tenant, calendar)
    await _salon(client, tenant)  # the service and the hours are configuration
    await client.get("/api/v1/agent", headers=_auth(tenant))
    async with async_session_factory() as session:
        await session.execute(
            update(Agent).where(Agent.business_id == business_id).values(active=True)
        )
        await session.commit()
    await _seed_chat(business_id)
    for number, event in enumerate(("evt-1", "evt-2", "evt-3")):
        _put_event(calendar, refresh, event)
        await _booking(
            business_id, number, event, "confirmed" if number < 2 else "needs_reconciliation"
        )
    await _booking(business_id, 5, None, "failed")  # nothing at Google for this one
    # A usage counter from a past day goes; today's stays.
    async with async_session_factory() as session:
        session.add(
            UsageCounter(
                business_id=business_id,
                kind="gemini_request",
                window_date=usage.day.date() - timedelta(days=1),
                count=9,
            )
        )
        await session.commit()
    assert await _count(UsageEvent, business_id) > 0  # the calendar calls above
    # Another business with its own data and (never cancelled) events.
    await _seed_chat(other.business_id)
    await _booking(other.business_id, 0, "other-evt")
    other_events_before = await _count(Booking, other.business_id)

    response = await _delete(client, tenant)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "interactions",
        "messages",
        "bookings",
        "escalations",
        "usage_events",
        "calendar_events_cancelled",
        "calendar_events_not_cancelled",
    }
    assert (body["interactions"], body["messages"], body["bookings"]) == (2, 4, 4)
    assert body["escalations"] == 0
    assert body["usage_events"] > 0
    assert (body["calendar_events_cancelled"], body["calendar_events_not_cancelled"]) == (3, 0)
    # The events are really gone from the calendar...
    assert calendar.api.events(refresh) == {}
    assert len(calendar.api.calls_to("delete_event")) == 3
    # ...and so are the rows, the events and the past counters.
    assert await _count(Interaction, business_id) == 0
    assert await _count(InteractionMessage, business_id) == 0
    assert await _count(Booking, business_id) == 0
    assert await _count(UsageEvent, business_id) == 0
    async with async_session_factory() as session:
        days = set(
            (
                await session.scalars(
                    select(UsageCounter.window_date).where(UsageCounter.business_id == business_id)
                )
            ).all()
        )
    assert days == {usage.day.date()}  # today's counters stay: a cap can't be reset this way
    # Configuration stays, and a live agent stays live.
    assert await _count(Service, business_id) == 1
    assert await _count(CalendarConnection, business_id) == 1
    agent = (await client.get("/api/v1/agent", headers=_auth(tenant))).json()
    assert agent["active"] is True
    status = (await client.get("/api/v1/calendar/status", headers=_auth(tenant))).json()
    assert status["status"] == "connected"
    hours = (await client.get("/api/v1/business/hours", headers=_auth(tenant))).json()
    assert hours["configured"] is True
    # The other business is untouched.
    assert await _count(Interaction, other.business_id) == 2
    assert await _count(Booking, other.business_id) == other_events_before == 1


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"confirmation": ""}, id="empty"),
        pytest.param({"confirmation": "delete test data"}, id="wrong-case"),
        pytest.param({"confirmation": "DELETE"}, id="partial"),
    ],
)
async def test_a_wrong_phrase_is_422_and_touches_nothing(client, entitled_tenants, calendar, body):
    tenant, _ = entitled_tenants
    refresh = await _connect(client, tenant, calendar)
    await _seed_chat(tenant.business_id)
    _put_event(calendar, refresh, "evt-1")
    await _booking(tenant.business_id, 0, "evt-1")
    calls_before = len(calendar.api.calls)

    response = await _delete(client, tenant, body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "confirmation_mismatch"
    assert len(calendar.api.calls) == calls_before  # no provider call
    assert len(calendar.api.events(refresh)) == 1
    assert await _count(Interaction, tenant.business_id) == 2
    assert await _count(Booking, tenant.business_id) == 1


async def test_a_missing_confirmation_is_a_validation_error(client, entitled_tenants):
    tenant, _ = entitled_tenants

    response = await client.request("DELETE", URL, json={}, headers=_auth(tenant))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_an_event_that_is_already_gone_counts_as_cancelled(
    client, entitled_tenants, calendar
):
    tenant, _ = entitled_tenants
    refresh = await _connect(client, tenant, calendar)
    _put_event(calendar, refresh, "still-there")
    await _booking(tenant.business_id, 0, "removed-by-the-owner")  # not at Google any more
    await _booking(tenant.business_id, 1, "still-there")

    response = await _delete(client, tenant)

    body = response.json()
    assert (body["calendar_events_cancelled"], body["calendar_events_not_cancelled"]) == (2, 0)
    assert body["bookings"] == 2


async def test_a_revoked_connection_stops_trying_but_the_data_is_still_deleted(
    client, entitled_tenants, calendar
):
    tenant, _ = entitled_tenants
    refresh = await _connect(client, tenant, calendar)
    for number in range(3):
        _put_event(calendar, refresh, f"evt-{number}")
        await _booking(tenant.business_id, number, f"evt-{number}")
    await _seed_chat(tenant.business_id)
    calendar.api.revoke_externally(refresh)
    refreshes = len(calendar.api.calls_to("refresh_access_token"))

    response = await _delete(client, tenant)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["calendar_events_cancelled"], body["calendar_events_not_cancelled"]) == (0, 3)
    assert (body["bookings"], body["interactions"]) == (3, 2)
    # One refresh failed; the other two events were never tried.
    assert len(calendar.api.calls_to("refresh_access_token")) == refreshes + 1
    assert calendar.api.calls_to("delete_event") == []
    status = (await client.get("/api/v1/calendar/status", headers=_auth(tenant))).json()
    assert status["status"] == "reconnect_needed"


async def test_without_a_selected_calendar_nothing_is_cancelled_but_data_is_deleted(
    client, entitled_tenants, calendar
):
    tenant, _ = entitled_tenants  # no calendar connected at all
    await _booking(tenant.business_id, 0, "evt-0")
    await _booking(tenant.business_id, 1, "evt-1")

    response = await _delete(client, tenant)

    body = response.json()
    assert (body["calendar_events_cancelled"], body["calendar_events_not_cancelled"]) == (0, 2)
    assert body["bookings"] == 2
    assert calendar.api.calls == []


async def test_a_failing_event_is_counted_and_the_rest_are_still_cancelled(
    client, entitled_tenants, calendar
):
    tenant, _ = entitled_tenants
    refresh = await _connect(client, tenant, calendar)
    for number in range(3):
        _put_event(calendar, refresh, f"evt-{number}")
        await _booking(tenant.business_id, number, f"evt-{number}")
    # The first delete fails for good (not retryable).
    calendar.api.fail_next("delete_event", GoogleApiError("server_error"))

    response = await _delete(client, tenant)

    body = response.json()
    assert response.status_code == 200, response.text
    assert (body["calendar_events_cancelled"], body["calendar_events_not_cancelled"]) == (2, 1)
    assert body["bookings"] == 3
    assert sorted(event_id for _, event_id in calendar.api.events(refresh)) == ["evt-0"]


async def test_another_businesss_events_are_never_cancelled(client, entitled_tenants, calendar):
    tenant_a, tenant_b = entitled_tenants
    refresh_a = await _connect(client, tenant_a, calendar)
    refresh_b = await _connect(client, tenant_b, calendar)
    _put_event(calendar, refresh_a, "a-evt")
    _put_event(calendar, refresh_b, "b-evt")
    await _booking(tenant_a.business_id, 0, "a-evt")
    await _booking(tenant_b.business_id, 0, "b-evt")

    response = await _delete(client, tenant_a)

    assert response.json()["calendar_events_cancelled"] == 1
    assert list(calendar.api.events(refresh_a)) == []
    assert list(calendar.api.events(refresh_b)) == [(OWNED.id, "b-evt")]
    assert await _count(Booking, tenant_b.business_id) == 1


async def test_deleting_twice_is_harmless(client, entitled_tenants, calendar):
    tenant, _ = entitled_tenants
    await _seed_chat(tenant.business_id)

    first = await _delete(client, tenant)
    second = await _delete(client, tenant)

    assert first.json()["interactions"] == 2
    assert second.status_code == 200
    assert second.json() == {
        "interactions": 0,
        "messages": 0,
        "bookings": 0,
        "escalations": 0,
        "usage_events": 0,
        "calendar_events_cancelled": 0,
        "calendar_events_not_cancelled": 0,
    }
