"""The E2E fake-provider mode (slice 17): the scripted model through the REAL conversation
engine and Postgres, the checkout and calendar fakes' browser-facing behavior, and the
journey's model request count (the number the E2E usage cap is sized from).
"""

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from core.billing import get_billing_gateway
from core.booking_rules import default_booking_rules
from core.calendar import get_calendar_api, get_clock
from core.db import async_session_factory
from core.llm import get_llm_client
from integrations.google_calendar.e2e import E2E_CALENDAR_ID, E2EGoogleCalendarApi
from integrations.llm.e2e import NO_INFO, E2EScriptedLLM
from integrations.stripe.e2e import E2EBillingGateway
from models.billing import Subscription
from models.booking import Booking
from services.booking_rules import BookingRules
from services.usage import UsageKind

# Wednesday 2026-10-07, 14:00 in New York.
NOW = datetime(2026, 10, 7, 18, 0, tzinfo=UTC)
FAQ_ANSWER = "We are closed on public holidays but open every other weekday."


class CountingLLM(E2EScriptedLLM):
    """The scripted model, counting provider calls like `FakeLLMClient.requests`."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        return await super().generate(request)


def _auth(tenant) -> dict[str, str]:
    return {"Authorization": f"Bearer {tenant.token}"}


@pytest.fixture
def scripted() -> CountingLLM:
    return CountingLLM()


@pytest.fixture
def e2e_calendar() -> E2EGoogleCalendarApi:
    return E2EGoogleCalendarApi()


@pytest.fixture
async def client(app, scripted, e2e_calendar, calendar):
    app.dependency_overrides[get_llm_client] = lambda: scripted
    app.dependency_overrides[get_calendar_api] = lambda: e2e_calendar
    app.dependency_overrides[get_clock] = lambda: lambda: NOW
    app.dependency_overrides[default_booking_rules] = lambda: BookingRules(15, 120, 60, 0)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _salon(client, tenant) -> str:
    week = {
        "days": [
            {"day_of_week": d, "is_closed": True}
            if d in (5, 6)
            else {"day_of_week": d, "is_closed": False, "opens_at": "09:00", "closes_at": "17:00"}
            for d in range(7)
        ]
    }
    assert (
        await client.put("/api/v1/business/hours", json=week, headers=_auth(tenant))
    ).status_code == 200
    faq = await client.post(
        "/api/v1/faqs",
        json={"question": "What is your holiday policy?", "answer": FAQ_ANSWER},
        headers=_auth(tenant),
    )
    assert faq.status_code == 201, faq.text
    created = await client.post(
        "/api/v1/services",
        json={"name": "Haircut", "duration_minutes": 45, "price": "40"},
        headers=_auth(tenant),
    )
    assert created.status_code == 201, created.text
    # The E2E flow: the consent URL is the web callback itself; the browser follows it.
    connect = await client.get("/api/v1/calendar/connect", headers=_auth(tenant))
    callback = urlparse(connect.json()["url"])
    query = parse_qs(callback.query)
    done = await client.post(
        "/api/v1/calendar/callback",
        json={"code": query["code"][0], "state": query["state"][0]},
        headers=_auth(tenant),
    )
    assert done.status_code == 200, done.text
    calendars = (await client.get("/api/v1/calendar/calendars", headers=_auth(tenant))).json()
    assert [c["id"] for c in calendars["calendars"]] == [E2E_CALENDAR_ID]
    picked = await client.patch(
        "/api/v1/calendar", json={"calendar_id": E2E_CALENDAR_ID}, headers=_auth(tenant)
    )
    assert picked.status_code == 200, picked.text
    return created.json()["id"]


async def _say(client, tenant, message: str, interaction_id: str | None = None) -> dict:
    body = {"message": message, **({"interaction_id": interaction_id} if interaction_id else {})}
    response = await client.post("/api/v1/agent/test", json=body, headers=_auth(tenant))
    assert response.status_code == 200, response.text
    return response.json()


async def test_the_journey_through_the_real_engine(
    client, entitled_tenants, scripted, e2e_calendar
):
    tenant, other = entitled_tenants
    service_id = await _salon(client, tenant)

    # FAQ: search_faq, then the FAQ's own answer, cited (2 model requests).
    faq = await _say(client, tenant, "What is your holiday policy?")
    assert (faq["reply_text"], faq["intent"], faq["fallback"]) == (FAQ_ANSWER, "FAQ", False)
    assert scripted.calls == 2

    # Something the FAQs don't cover: the honest line, not a made-up answer.
    scripted.calls = 0
    unknown = await _say(client, tenant, "Do you sell gift cards?")
    assert unknown["reply_text"] == NO_INFO and unknown["fallback"] is False
    assert scripted.calls == 2

    # Booking, everything in one message: get_services, check_availability,
    # create_booking, confirmation (4 requests).
    scripted.calls = 0
    chat = faq["interaction_id"]
    booked = await _say(
        client,
        tenant,
        "I would like to book a Haircut tomorrow at 10:00. My name is Sam Tester.",
        chat,
    )
    assert booked["fallback"] is False
    assert booked["reply_text"] == (
        "You're booked: Haircut on Thursday, October 8 at 10:00 AM, under the name Sam Tester."
    )
    assert scripted.calls == 4
    async with async_session_factory() as session:
        [row] = (
            await session.execute(select(Booking).where(Booking.business_id == tenant.business_id))
        ).scalars()
    assert (row.status, row.customer_name, str(row.service_id)) == (
        "confirmed",
        "Sam Tester",
        service_id,
    )
    [refresh] = {t for t in e2e_calendar._token_account}  # one account, one grant
    [event] = e2e_calendar.events(refresh).values()
    assert event.id == row.calendar_event_id

    # Escalation: one request, recorded by the policy.
    scripted.calls = 0
    manager = await _say(client, tenant, "I want to speak to a manager", chat)
    assert (manager["intent"], manager["escalated"]) == ("HUMAN_REQUEST", True)
    assert "team will be notified" in manager["reply_text"] and scripted.calls == 1

    # Ending the chat writes the two-sentence summary (1 request); the detail shows it.
    scripted.calls = 0
    ended = await client.post(f"/api/v1/agent/test/{chat}/end", headers=_auth(tenant))
    assert ended.status_code == 200, ended.text
    assert scripted.calls == 1
    detail = (await client.get(f"/api/v1/interactions/{chat}", headers=_auth(tenant))).json()
    assert detail["summary_source"] == "ai"
    assert detail["summary"] == (
        "The customer asked to book a Haircut. "
        "The appointment was booked for Thu 8 Oct at 10:00 AM."
    )
    assert [e["reason"] for e in detail["escalations"]] == ["human_requested"]
    # Another business never sees it.
    foreign = await client.get(f"/api/v1/interactions/{chat}", headers=_auth(other))
    assert foreign.status_code == 404


async def test_a_complaint_is_escalated_and_a_missing_name_is_asked_for(
    client, entitled_tenants, scripted
):
    tenant, _ = entitled_tenants
    await _salon(client, tenant)

    ask = await _say(client, tenant, "Can I book a haircut on 2026-10-09 at 2:30 pm?")
    assert ask["reply_text"].startswith("To book Haircut on Friday, October 9 at 2:30 PM")
    assert ask["fallback"] is False
    chat = ask["interaction_id"]

    done = await _say(client, tenant, "Sam Tester", chat)
    assert done["reply_text"] == (
        "You're booked: Haircut on Friday, October 9 at 2:30 PM, under the name Sam Tester."
    )

    complaint = await _say(client, tenant, "I have a complaint about my last visit", chat)
    assert (complaint["intent"], complaint["escalated"]) == ("COMPLAINT", True)


async def test_an_unoffered_time_is_never_repeated(client, entitled_tenants):
    tenant, _ = entitled_tenants
    await _salon(client, tenant)

    reply = await _say(client, tenant, "Book a haircut tomorrow at 7:00 am, I'm Sam Tester")

    assert reply["fallback"] is False
    assert reply["reply_text"].startswith(
        "Sorry, that time isn't available. On Thursday, October 8 I have"
    )
    assert "7:00" not in reply["reply_text"]


async def test_the_quota_fallback_after_the_business_cap(client, entitled_tenants, usage):
    tenant, _ = entitled_tenants
    usage.set_daily(UsageKind.GEMINI_REQUEST, per_day=1000, per_business_per_day=4)

    first = await _say(client, tenant, "What is your holiday policy?")
    second = await _say(client, tenant, "Do you sell gift cards?", first["interaction_id"])
    capped = await _say(client, tenant, "Do you sell gift cards?", first["interaction_id"])

    assert first["usage_limited"] is False and second["usage_limited"] is False
    assert capped["usage_limited"] is True and capped["fallback"] is True
    assert "can't help with that right now" in capped["reply_text"]


async def test_checkout_completes_through_the_stripe_url_the_web_client_follows(app, two_tenants):
    gateway = E2EBillingGateway()
    app.dependency_overrides[get_billing_gateway] = lambda: gateway
    tenant, _ = two_tenants
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        started = await client.post(
            "/api/v1/billing/checkout", json={"choice": "trial"}, headers=_auth(tenant)
        )
        assert started.status_code == 200, started.text
        url = urlparse(started.json()["url"])
        assert (url.scheme, url.hostname) == ("https", "checkout.stripe.com")
        session_id = url.path.rsplit("/", 1)[-1]
        # What the browser test does after intercepting that URL: the return page syncs.
        synced = await client.post(
            f"/api/v1/billing/checkout/{session_id}/sync", headers=_auth(tenant)
        )
        assert synced.status_code == 200, synced.text
        gated = await client.get("/api/v1/services", headers=_auth(tenant))
        assert gated.status_code == 200  # no longer 402
    async with async_session_factory() as session:
        row = (
            await session.execute(
                select(Subscription).where(Subscription.business_id == tenant.business_id)
            )
        ).scalar_one()
    assert row.status == "trialing"
    portal = await gateway.create_portal_session(customer_id="cus_1", return_url="x")
    assert urlparse(portal).hostname == "billing.stripe.com"


async def test_the_calendar_consent_url_points_back_at_the_web_callback():
    api = E2EGoogleCalendarApi()
    url = api.authorization_url(
        state="st", code_challenge="cc", redirect_uri="http://localhost:3000/cb"
    )
    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "http://localhost:3000/cb"
    assert parse_qs(parsed.query)["state"] == ["st"]
    grant = await api.exchange_code(
        code="x", code_verifier="v", redirect_uri="http://localhost:3000/cb"
    )
    assert grant.refresh_token and grant.scopes
    [calendar] = await api.list_calendars(grant.access_token)
    assert calendar.id == E2E_CALENDAR_ID and calendar.access_role == "owner"
