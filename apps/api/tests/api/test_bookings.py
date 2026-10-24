"""The slice 11 demo end to end against real Postgres: the test chat (`POST /agent/test`,
fake model) -> `create_booking` -> `BookingService` -> availability recheck ->
`CalendarConnections.create_event` -> the metered gateway -> the fake Google API ->
the `bookings` row -> `GET /bookings?interaction_id=` (the side panel).
"""

from datetime import UTC, date, datetime
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from core.booking_rules import default_booking_rules
from core.calendar import get_clock
from core.db import async_session_factory
from integrations.google_calendar.fake import fake_calendar
from integrations.google_calendar.port import GoogleApiError
from integrations.llm.fake import agent_turn
from models.booking import Booking
from services.booking_rules import BookingRules
from services.booking_tool import CREATE_BOOKING
from tests.services.fake_busy_source import local

OWNED = fake_calendar("work@example.com", "Work", primary=True)
# Wednesday 2026-10-07, 14:00 in New York.
NOW = datetime(2026, 10, 7, 18, 0, tzinfo=UTC)
FRIDAY = date(2026, 10, 9)
BOOKED = "You're booked for a haircut on Friday at 3:30 PM, Alex."


def _auth(tenant) -> dict[str, str]:
    return {"Authorization": f"Bearer {tenant.token}"}


@pytest.fixture
async def client(app):
    app.dependency_overrides[get_clock] = lambda: lambda: NOW
    app.dependency_overrides[default_booking_rules] = lambda: BookingRules(15, 120, 60, 0)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _salon(client, tenant) -> str:
    """Hours Mon-Fri 09:00-17:00 and a 45-minute Haircut; returns the service id."""
    week = {
        "days": [
            {"day_of_week": d, "is_closed": True}
            if d in (5, 6)
            else {"day_of_week": d, "is_closed": False, "opens_at": "09:00", "closes_at": "17:00"}
            for d in range(7)
        ]
    }
    hours = await client.put("/api/v1/business/hours", json=week, headers=_auth(tenant))
    assert hours.status_code == 200, hours.text
    created = await client.post(
        "/api/v1/services",
        json={"name": "Haircut", "duration_minutes": 45, "price": "40"},
        headers=_auth(tenant),
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def _connect(client, tenant, calendar) -> str:
    refresh = calendar.api.issue_grant(calendars=[OWNED])
    connect = await client.get("/api/v1/calendar/connect", headers=_auth(tenant))
    state = parse_qs(urlparse(connect.json()["url"]).query)["state"][0]
    done = await client.post(
        "/api/v1/calendar/callback",
        json={"code": "auth-code-1", "state": state},
        headers=_auth(tenant),
    )
    assert done.status_code == 200, done.text
    picked = await client.patch(
        "/api/v1/calendar", json={"calendar_id": OWNED.id}, headers=_auth(tenant)
    )
    assert picked.status_code == 200, picked.text
    assert refresh is not None
    return refresh


def _book_call() -> dict:
    arguments = {
        "service": "haircut",
        "appointment_date": FRIDAY.isoformat(),
        "start_time": "15:30",
        "customer_name": "Alex",
    }
    return agent_turn("Booking.", tool_calls=[{"name": CREATE_BOOKING, "arguments": arguments}])


async def _chat(client, tenant, message: str) -> dict:
    response = await client.post(
        "/api/v1/agent/test", json={"message": message}, headers=_auth(tenant)
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _bookings(client, tenant, interaction_id: str):
    return await client.get(
        "/api/v1/bookings", params={"interaction_id": interaction_id}, headers=_auth(tenant)
    )


async def test_demo_books_a_verified_appointment(client, entitled_tenants, calendar, llm):
    tenant, other = entitled_tenants
    service_id = await _salon(client, tenant)
    refresh = await _connect(client, tenant, calendar)
    llm.queue(_book_call(), agent_turn(BOOKED, intent="BOOK_APPOINTMENT", sources=[service_id]))

    turn = await _chat(client, tenant, "3:30 please, I'm Alex")

    assert (turn["reply_text"], turn["fallback"]) == (BOOKED, False)
    # The event is on the owner's calendar, for the stored service's 45 minutes.
    [event] = calendar.api.events(refresh).values()
    assert (event.start, event.end) == (local(FRIDAY, "15:30"), local(FRIDAY, "16:15"))
    async with async_session_factory() as session:
        [row] = (
            await session.execute(select(Booking).where(Booking.business_id == tenant.business_id))
        ).scalars()
    assert row.status == "confirmed" and row.calendar_event_id == event.id
    assert str(row.interaction_id) == turn["interaction_id"]
    assert str(row.service_id) == service_id

    listed = await _bookings(client, tenant, turn["interaction_id"])
    assert listed.status_code == 200
    [booking] = listed.json()["bookings"]
    assert booking == {
        "id": str(row.id),
        "service_name": "Haircut",
        "start": "2026-10-09T15:30:00-04:00",
        "end": "2026-10-09T16:15:00-04:00",
        "date": "2026-10-09",
        "label": "15:30",
        "customer_name": "Alex",
        "status": "confirmed",
    }
    # Another business never sees it, even with the interaction id.
    foreign = await _bookings(client, other, turn["interaction_id"])
    assert foreign.status_code == 200 and foreign.json() == {"bookings": []}


async def test_a_grant_revoked_while_booking_confirms_nothing(
    client, entitled_tenants, calendar, llm
):
    tenant, _ = entitled_tenants
    service_id = await _salon(client, tenant)
    refresh = await _connect(client, tenant, calendar)
    calendar.api.fail_next("insert_event", GoogleApiError("unauthorized", auth_revoked=True))
    honest = "I'm sorry, I couldn't complete that booking, so nothing is booked yet."
    llm.queue(_book_call(), agent_turn(honest, intent="BOOK_APPOINTMENT", sources=[service_id]))

    turn = await _chat(client, tenant, "3:30 please, I'm Alex")

    assert turn["reply_text"] == honest
    assert calendar.api.events(refresh) == {}
    [booking] = (await _bookings(client, tenant, turn["interaction_id"])).json()["bookings"]
    assert booking["status"] == "failed"
    status = (await client.get("/api/v1/calendar/status", headers=_auth(tenant))).json()
    assert status["status"] == "reconnect_needed"  # committed before the turn ended


async def test_a_calendar_outage_never_shows_as_booked(client, entitled_tenants, calendar, llm):
    tenant, _ = entitled_tenants
    service_id = await _salon(client, tenant)
    refresh = await _connect(client, tenant, calendar)
    calendar.api.fail_next("insert_event", GoogleApiError("server_error", retryable=True), times=2)
    llm.queue(
        _book_call(),
        agent_turn("You're all set!", intent="BOOK_APPOINTMENT", sources=[service_id]),
        agent_turn("You're booked!", intent="BOOK_APPOINTMENT", sources=[service_id]),
    )

    turn = await _chat(client, tenant, "3:30 please, I'm Alex")

    assert turn["fallback"] is True
    assert "can't confirm a booking" in turn["reply_text"]
    assert calendar.api.events(refresh) == {}
    [booking] = (await _bookings(client, tenant, turn["interaction_id"])).json()["bookings"]
    assert booking["status"] == "failed"


async def test_validation(client, entitled_tenants):
    tenant, _ = entitled_tenants
    missing = await client.get("/api/v1/bookings", headers=_auth(tenant))
    assert (missing.status_code, missing.json()["error"]["code"]) == (422, "validation_error")
    bad = await client.get(
        "/api/v1/bookings", params={"interaction_id": "nope"}, headers=_auth(tenant)
    )
    assert bad.status_code == 422


async def test_requires_an_active_plan_and_a_login(client, two_tenants):
    unpaid, _ = two_tenants  # onboarded, never started a trial
    params = {"interaction_id": "00000000-0000-0000-0000-000000000000"}
    paid = await client.get("/api/v1/bookings", params=params, headers=_auth(unpaid))
    assert (paid.status_code, paid.json()["error"]["code"]) == (402, "subscription_required")
    assert (await client.get("/api/v1/bookings", params=params)).status_code == 401
