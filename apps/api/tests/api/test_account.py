"""`DELETE /api/v1/account` over HTTP on real Postgres with the fake Stripe, Google Calendar
and Clerk: the order and the reports, what is deleted (the owner's whole footprint) and what is
not (another business, global usage rows), owners without a plan or a business, the 422 and
503 paths that delete nothing, and a repeat after a Clerk failure."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text

from core.account import get_clerk_users_api
from core.db import async_session_factory
from integrations.clerk.fake import FakeClerkUsersApi
from integrations.clerk.port import REASON_AUTH_FAILED, ClerkApiError
from integrations.google_calendar.port import CreatedEvent
from integrations.stripe.port import GatewayNotFoundError
from models.agent import Agent
from models.billing import Subscription
from models.booking import Booking
from models.business import Business
from models.calendar import CalendarConnection
from models.interaction import Interaction
from models.service import Service
from models.usage import UsageCounter, UsageEvent
from models.user import User
from tests.api.inbox_factory import add_interaction
from tests.api.test_bookings import NOW, OWNED, _auth, _connect, _salon, client  # noqa: F401

# The `client` fixture is imported by name, which pytest needs and ruff reads as a
# redefinition.
# ruff: noqa: F811

URL = "/api/v1/account"
PHRASE = {"confirmation": "DELETE MY ACCOUNT"}


@pytest.fixture
def clerk(app) -> FakeClerkUsersApi:
    api = FakeClerkUsersApi()
    app.dependency_overrides[get_clerk_users_api] = lambda: api
    return api


async def _delete(client, tenant, body=PHRASE):
    return await client.request("DELETE", URL, json=body, headers=_auth(tenant))


async def _booking(
    business_id: uuid.UUID, starts_in: timedelta, event: str | None, status: str = "confirmed"
) -> None:
    start = NOW + starts_in
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
                created_at=NOW - timedelta(days=10),
            )
        )
        await session.commit()


def _put_event(calendar, refresh: str, event_id: str, starts_in: timedelta) -> None:
    start = NOW + starts_in
    calendar.api.events(refresh)[(OWNED.id, event_id)] = CreatedEvent(
        id=event_id, html_link=None, start=start, end=start + timedelta(minutes=45)
    )


async def _count(model, business_id: uuid.UUID) -> int:
    async with async_session_factory() as session:
        return int(
            await session.scalar(
                select(func.count()).select_from(model).where(model.business_id == business_id)
            )
            or 0
        )


async def _user_exists(external_auth_id: str) -> bool:
    async with async_session_factory() as session:
        return (
            await session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.external_auth_id == external_auth_id)
            )
        ) == 1


async def _business_exists(business_id: uuid.UUID) -> bool:
    async with async_session_factory() as session:
        return await session.get(Business, business_id) is not None


async def _tenant_rows(business_id: uuid.UUID) -> int:
    """Rows across every tenant table, found by the generic `business_id` column."""
    async with async_session_factory() as session:
        tables = (
            await session.scalars(
                text(
                    "SELECT table_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND column_name = 'business_id'"
                )
            )
        ).all()
        total = 0
        for table in tables:
            total += int(
                await session.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE business_id = :b"),  # noqa: S608
                    {"b": business_id},
                )
                or 0
            )
        return total


async def _customer_id(business_id: uuid.UUID) -> str:
    async with async_session_factory() as session:
        customer = await session.scalar(
            select(Subscription.stripe_customer_id).where(Subscription.business_id == business_id)
        )
    assert customer
    return customer


async def test_it_deletes_the_whole_footprint_and_nothing_of_anyone_else(
    client, entitled_tenants, calendar, billing_gateway, clerk, usage
):
    tenant, other = entitled_tenants
    business_id = tenant.business_id
    refresh = await _connect(client, tenant, calendar)
    other_refresh = await _connect(client, other, calendar)
    await _salon(client, tenant)
    await client.get("/api/v1/agent", headers=_auth(tenant))
    await add_interaction(business_id)
    await add_interaction(business_id, channel="voice")
    customer = await _customer_id(business_id)
    other_customer = await _customer_id(other.business_id)
    # Two upcoming events (one needs reconciliation), one that already happened, a failed
    # booking with no event, and an upcoming cancelled one the calendar no longer has.
    for name, starts_in, status in (
        ("upcoming-1", timedelta(days=2), "confirmed"),
        ("upcoming-2", timedelta(days=9), "needs_reconciliation"),
        ("past-1", timedelta(days=-3), "confirmed"),
    ):
        _put_event(calendar, refresh, name, starts_in)
        await _booking(business_id, starts_in, name, status)
    await _booking(business_id, timedelta(days=4), None, "failed")
    await _booking(business_id, timedelta(days=5), "was-cancelled", "cancelled")
    # Another business with its own data, plus a global usage row.
    await add_interaction(other.business_id)
    _put_event(calendar, other_refresh, "other-evt", timedelta(days=2))
    await _booking(other.business_id, timedelta(days=2), "other-evt")
    async with async_session_factory() as session:
        session.add(
            UsageCounter(business_id=None, kind="sms", window_date=usage.day.date(), count=7)
        )
        await session.commit()
    assert await _tenant_rows(business_id) > 10
    other_rows = await _tenant_rows(other.business_id)

    response = await _delete(client, tenant)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "calendar_events_cancelled": 2,
        "calendar_events_not_cancelled": 0,
        "calendar_token_revoked": True,
        "sign_in_deleted": True,
    }
    # Stripe first: only this business's customer, never the other's.
    assert billing_gateway.deleted_customers == [customer]
    assert other_customer not in billing_gateway.deleted_customers
    # Only the UPCOMING events are gone; the past one stays on the owner's calendar.
    assert sorted(event_id for _, event_id in calendar.api.events(refresh)) == ["past-1"]
    assert len(calendar.api.calls_to("delete_event")) == 2
    assert refresh in calendar.api.revoked and other_refresh not in calendar.api.revoked
    assert list(calendar.api.events(other_refresh)) == [(OWNED.id, "other-evt")]
    # Clerk, once, for this user only.
    assert clerk.deleted == [tenant.external_auth_id]
    # The footprint is gone: user, business, and every row under it.
    assert not await _user_exists(tenant.external_auth_id)
    assert not await _business_exists(business_id)
    assert await _tenant_rows(business_id) == 0
    for model in (Interaction, Booking, Service, Agent, CalendarConnection, Subscription):
        assert await _count(model, business_id) == 0, model
    # The other business is untouched.
    assert await _user_exists(other.external_auth_id)
    assert await _business_exists(other.business_id)
    assert await _tenant_rows(other.business_id) == other_rows
    # The global usage row is untouched, and the Clerk call was metered with no business.
    async with async_session_factory() as session:
        counters = {
            kind: count
            for kind, count in (
                await session.execute(
                    select(UsageCounter.kind, UsageCounter.count).where(
                        UsageCounter.business_id.is_(None),
                        UsageCounter.window_date == usage.day.date(),
                    )
                )
            ).all()
        }
        identity_events = (
            await session.scalars(
                select(UsageEvent).where(
                    UsageEvent.event_type == "identity_call",
                    UsageEvent.created_at >= usage.day - timedelta(days=1),
                    UsageEvent.created_at < usage.day + timedelta(days=1),
                )
            )
        ).all()
    assert counters["sms"] == 7
    assert counters["identity_call"] == 1
    assert [(e.business_id, e.provider) for e in identity_events] == [(None, "clerk")]


async def test_repeating_the_request_is_harmless_and_deletes_the_recreated_user(
    client, entitled_tenants, clerk
):
    tenant, _ = entitled_tenants

    first = await _delete(client, tenant)
    # The same token is valid for a moment: the next request re-creates the `users` row.
    second = await _delete(client, tenant)

    assert first.status_code == 200 and second.status_code == 200
    assert second.json() == {
        "calendar_events_cancelled": 0,
        "calendar_events_not_cancelled": 0,
        "calendar_token_revoked": None,
        "sign_in_deleted": True,
    }
    assert clerk.deleted == [tenant.external_auth_id]  # the repeat found nothing at Clerk
    assert clerk.calls == [tenant.external_auth_id] * 2
    assert not await _user_exists(tenant.external_auth_id)


async def test_an_owner_without_a_plan_can_delete_and_no_stripe_call_is_made(
    client, two_tenants, billing_gateway, clerk
):
    tenant, _ = two_tenants  # onboarded, never started a trial: no subscription row

    response = await _delete(client, tenant)

    assert response.status_code == 200, response.text
    assert response.json()["sign_in_deleted"] is True
    assert billing_gateway.deleted_customers == []
    assert not await _business_exists(tenant.business_id)
    assert not await _user_exists(tenant.external_auth_id)
    assert clerk.deleted == [tenant.external_auth_id]


async def test_an_owner_who_never_created_a_business_deletes_just_the_user_and_the_sign_in(
    client, mint_token, billing_gateway, clerk
):
    subject = f"user_{uuid.uuid4().hex}"
    token = mint_token(subject=subject, email=f"new.{uuid.uuid4().hex[:8]}@example.com")

    response = await client.request(
        "DELETE", URL, json=PHRASE, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "calendar_events_cancelled": 0,
        "calendar_events_not_cancelled": 0,
        "calendar_token_revoked": None,
        "sign_in_deleted": True,
    }
    assert billing_gateway.deleted_customers == []
    assert not await _user_exists(subject)
    assert clerk.deleted == [subject]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"confirmation": ""}, id="empty"),
        pytest.param({"confirmation": "delete my account"}, id="wrong-case"),
        pytest.param({"confirmation": "DELETE TEST DATA"}, id="the-other-phrase"),
    ],
)
async def test_a_wrong_phrase_is_422_and_deletes_nothing(
    client, entitled_tenants, calendar, billing_gateway, clerk, body
):
    tenant, _ = entitled_tenants
    refresh = await _connect(client, tenant, calendar)
    _put_event(calendar, refresh, "evt-1", timedelta(days=2))
    await _booking(tenant.business_id, timedelta(days=2), "evt-1")
    await add_interaction(tenant.business_id)
    calls_before = len(calendar.api.calls)

    response = await _delete(client, tenant, body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "confirmation_mismatch"
    assert billing_gateway.deleted_customers == [] and clerk.calls == []
    assert len(calendar.api.calls) == calls_before
    assert len(calendar.api.events(refresh)) == 1
    assert await _business_exists(tenant.business_id)
    assert await _user_exists(tenant.external_auth_id)
    assert await _count(Interaction, tenant.business_id) == 1


async def test_a_missing_confirmation_is_a_validation_error(client, entitled_tenants, clerk):
    tenant, _ = entitled_tenants

    response = await client.request("DELETE", URL, json={}, headers=_auth(tenant))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert clerk.calls == []


async def test_it_needs_a_signed_in_user(client, clerk):
    response = await client.request("DELETE", URL, json=PHRASE)

    assert response.status_code == 401
    assert clerk.calls == []


async def test_a_stripe_outage_is_503_and_nothing_at_all_is_deleted(
    client, entitled_tenants, calendar, billing_gateway, clerk
):
    tenant, _ = entitled_tenants
    refresh = await _connect(client, tenant, calendar)
    _put_event(calendar, refresh, "evt-1", timedelta(days=2))
    await _booking(tenant.business_id, timedelta(days=2), "evt-1")
    await add_interaction(tenant.business_id)
    billing_gateway.fail_next_delete = RuntimeError("stripe is down")
    rows = await _tenant_rows(tenant.business_id)
    calls_before = len(calendar.api.calls)

    response = await _delete(client, tenant)

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "billing_unreachable"
    assert "nothing was deleted" in error["message"]
    assert "stripe is down" not in response.text
    assert clerk.calls == []
    assert len(calendar.api.calls) == calls_before and calendar.api.revoked == set()
    assert len(calendar.api.events(refresh)) == 1
    assert await _tenant_rows(tenant.business_id) == rows
    assert await _user_exists(tenant.external_auth_id)

    # Once Stripe is back, the same request goes through.
    retry = await _delete(client, tenant)

    assert retry.status_code == 200, retry.text
    assert not await _business_exists(tenant.business_id)
    assert clerk.deleted == [tenant.external_auth_id]


async def test_a_stripe_customer_that_is_already_gone_does_not_block_the_deletion(
    client, entitled_tenants, billing_gateway, clerk
):
    tenant, _ = entitled_tenants
    billing_gateway.fail_next_delete = GatewayNotFoundError("cus_gone")

    response = await _delete(client, tenant)

    assert response.status_code == 200, response.text
    assert not await _business_exists(tenant.business_id)
    assert clerk.deleted == [tenant.external_auth_id]


async def test_a_clerk_failure_is_reported_and_the_repeat_removes_the_sign_in(
    client, entitled_tenants, clerk
):
    tenant, _ = entitled_tenants
    clerk.fail_next(ClerkApiError(REASON_AUTH_FAILED))

    first = await _delete(client, tenant)

    assert first.status_code == 200, first.text
    assert first.json()["sign_in_deleted"] is False
    assert not await _business_exists(tenant.business_id)  # the data IS deleted
    assert clerk.deleted == []

    second = await _delete(client, tenant)

    assert second.status_code == 200 and second.json()["sign_in_deleted"] is True
    assert clerk.deleted == [tenant.external_auth_id]
    assert not await _user_exists(tenant.external_auth_id)


async def test_without_a_clerk_secret_key_the_data_goes_and_the_sign_in_is_reported(
    client, entitled_tenants, app
):
    app.dependency_overrides[get_clerk_users_api] = lambda: None
    tenant, _ = entitled_tenants

    response = await _delete(client, tenant)

    assert response.status_code == 200, response.text
    assert response.json()["sign_in_deleted"] is False
    assert not await _business_exists(tenant.business_id)
    assert not await _user_exists(tenant.external_auth_id)
