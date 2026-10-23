"""`GET /api/v1/calendar/availability` against a real Postgres, with Google replaced by
`FakeGoogleCalendarApi` (the `calendar` fixture) and the clock pinned.

The whole vertical path: router -> `AvailabilityService` -> `BusinessDirectory` (hours,
service, booking rules from the DB) + `CalendarConnections.free_busy` -> the metered
gateway -> the fake Google API.
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
from models.usage import UsageEvent
from services.booking_rules import BookingRules
from tests.services.fake_busy_source import local

OWNED = fake_calendar("work@example.com", "Work", primary=True)
TRANSIENT = GoogleApiError("server_error", retryable=True)
# Wednesday 2026-10-07, 14:00 in New York.
NOW = datetime(2026, 10, 7, 18, 0, tzinfo=UTC)
FRIDAY = date(2026, 10, 9)
SUNDAY = date(2026, 10, 11)


def _auth(tenant) -> dict[str, str]:
    return {"Authorization": f"Bearer {tenant.token}"}


@pytest.fixture
async def client(app):
    app.dependency_overrides[get_clock] = lambda: lambda: NOW
    app.dependency_overrides[default_booking_rules] = lambda: BookingRules(15, 120, 60, 0)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _week() -> dict:
    return {
        "days": [
            {"day_of_week": d, "is_closed": True}
            if d in (5, 6)
            else {"day_of_week": d, "is_closed": False, "opens_at": "09:00", "closes_at": "17:00"}
            for d in range(7)
        ]
    }


async def _salon(client, tenant) -> str:
    """Hours Mon-Fri 09:00-17:00 and a 45-minute Haircut; returns the service id."""
    headers = _auth(tenant)
    hours = await client.put("/api/v1/business/hours", json=_week(), headers=headers)
    assert hours.status_code == 200, hours.text
    created = await client.post(
        "/api/v1/services",
        json={"name": "Haircut", "duration_minutes": 45, "price": "40"},
        headers=headers,
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


async def _availability(client, tenant, service_id: str, day: date = FRIDAY):
    return await client.get(
        "/api/v1/calendar/availability",
        params={"service_id": service_id, "date": day.isoformat()},
        headers=_auth(tenant),
    )


async def _events(tenant, event_type: str) -> list[UsageEvent]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(UsageEvent).where(
                UsageEvent.business_id == tenant.business_id,
                UsageEvent.event_type == event_type,
            )
        )
        return list(result.scalars())


async def test_offers_real_free_slots_from_the_connected_calendar(
    client, entitled_tenants, calendar
):
    tenant, _ = entitled_tenants
    service_id = await _salon(client, tenant)
    refresh = await _connect(client, tenant, calendar)
    calendar.api.add_busy(refresh, OWNED.id, local(FRIDAY, "09:00"), local(FRIDAY, "12:00"))

    response = await _availability(client, tenant, service_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["reason"], body["timezone"]) == (
        "available",
        None,
        "America/New_York",
    )
    assert body["service"] == {"id": service_id, "name": "Haircut", "duration_minutes": 45}
    labels = [slot["label"] for slot in body["slots"]]
    assert labels[0] == "12:00" and labels[-1] == "16:15" and len(labels) == 18
    assert [slot["label"] for slot in body["offered"]] == ["12:00", "14:00", "16:15"]
    assert body["offered"][0]["start"] == "2026-10-09T12:00:00-04:00"
    assert body["offered"][0]["end"] == "2026-10-09T12:45:00-04:00"
    # One metered free/busy call (plus its token refresh), both counted.
    assert len(calendar.api.calls_to("free_busy")) == 1
    assert len(await _events(tenant, "calendar_call")) == len(calendar.api.calls)


async def test_owner_booking_rules_apply(client, entitled_tenants, calendar):
    tenant, _ = entitled_tenants
    service_id = await _salon(client, tenant)
    await _connect(client, tenant, calendar)
    saved = await client.put(
        "/api/v1/business/booking-rules",
        json={
            "slot_interval_minutes": 60,
            "min_notice_minutes": 0,
            "max_days_ahead": 30,
            "buffer_minutes": 0,
        },
        headers=_auth(tenant),
    )
    assert saved.status_code == 200, saved.text

    body = (await _availability(client, tenant, service_id)).json()
    assert [slot["label"] for slot in body["slots"]] == [
        "09:00",
        "10:00",
        "11:00",
        "12:00",
        "13:00",
        "14:00",
        "15:00",
        "16:00",
    ]


async def test_calendar_outage_offers_nothing_and_records_a_failed_request(
    client, entitled_tenants, calendar
):
    tenant, _ = entitled_tenants
    service_id = await _salon(client, tenant)
    await _connect(client, tenant, calendar)
    calendar.api.fail_next("free_busy", TRANSIENT, times=2)

    response = await _availability(client, tenant, service_id)

    body = response.json()
    assert response.status_code == 200
    assert (body["status"], body["reason"], body["slots"], body["offered"]) == (
        "calendar_unavailable",
        "unavailable",
        [],
        [],
    )
    assert len(calendar.api.calls_to("free_busy")) == 2  # bounded: two attempts
    assert len(await _events(tenant, "failed_request")) == 2
    status = (await client.get("/api/v1/calendar/status", headers=_auth(tenant))).json()
    assert status["status"] == "connected"  # an outage is not a reconnect


async def test_a_grant_revoked_at_google_needs_a_reconnect(client, entitled_tenants, calendar):
    tenant, _ = entitled_tenants
    service_id = await _salon(client, tenant)
    refresh = await _connect(client, tenant, calendar)
    calendar.api.revoke_externally(refresh)

    body = (await _availability(client, tenant, service_id)).json()

    assert (body["status"], body["reason"]) == ("calendar_unavailable", "reconnect_needed")
    status = (await client.get("/api/v1/calendar/status", headers=_auth(tenant))).json()
    assert status["status"] == "reconnect_needed"
    # Marked once; the next lookup doesn't even try Google.
    calls = len(calendar.api.calls)
    again = (await _availability(client, tenant, service_id)).json()
    assert again["reason"] == "reconnect_needed" and len(calendar.api.calls) == calls


async def test_not_configured_is_not_connected(client, entitled_tenants):
    # No `calendar` fixture: the feature isn't configured on this server.
    tenant, _ = entitled_tenants
    service_id = await _salon(client, tenant)
    body = (await _availability(client, tenant, service_id)).json()
    assert (body["status"], body["reason"]) == ("calendar_unavailable", "not_connected")


async def test_connected_without_a_chosen_calendar_is_not_connected(
    client, entitled_tenants, calendar
):
    tenant, _ = entitled_tenants
    service_id = await _salon(client, tenant)
    body = (await _availability(client, tenant, service_id)).json()
    assert (body["status"], body["reason"]) == ("calendar_unavailable", "not_connected")
    assert calendar.api.calls == []


async def test_closed_day_and_past_dates_never_call_google(client, entitled_tenants, calendar):
    tenant, _ = entitled_tenants
    service_id = await _salon(client, tenant)
    await _connect(client, tenant, calendar)
    calls = len(calendar.api.calls)

    closed = (await _availability(client, tenant, service_id, SUNDAY)).json()
    past = (await _availability(client, tenant, service_id, date(2026, 10, 1))).json()

    assert (closed["status"], closed["reason"]) == ("no_availability", "closed")
    assert (past["status"], past["reason"]) == ("no_availability", "past_date")
    assert len(calendar.api.calls) == calls


async def test_another_tenants_service_is_not_found(client, entitled_tenants, calendar):
    tenant_a, tenant_b = entitled_tenants
    service_a = await _salon(client, tenant_a)
    await _salon(client, tenant_b)
    await _connect(client, tenant_b, calendar)

    response = await _availability(client, tenant_b, service_a)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "service_not_found"
    assert calendar.api.calls_to("free_busy") == []


async def test_validation(client, entitled_tenants, calendar):
    tenant, _ = entitled_tenants
    service_id = await _salon(client, tenant)
    for params in (
        {"service_id": service_id, "date": "Friday"},
        {"service_id": "not-a-uuid", "date": "2026-10-09"},
        {"date": "2026-10-09"},
        {"service_id": service_id},
    ):
        response = await client.get(
            "/api/v1/calendar/availability", params=params, headers=_auth(tenant)
        )
        assert response.status_code == 422, params
        assert response.json()["error"]["code"] == "validation_error"


async def test_requires_an_active_plan_and_a_login(client, two_tenants, calendar):
    tenant, _ = two_tenants  # onboarded, never started a trial
    params = {"service_id": "00000000-0000-0000-0000-000000000000", "date": "2026-10-09"}
    paid = await client.get("/api/v1/calendar/availability", params=params, headers=_auth(tenant))
    assert (paid.status_code, paid.json()["error"]["code"]) == (402, "subscription_required")
    anonymous = await client.get("/api/v1/calendar/availability", params=params)
    assert anonymous.status_code == 401
