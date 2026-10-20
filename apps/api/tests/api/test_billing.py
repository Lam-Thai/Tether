"""Billing endpoints + Stripe webhook against a real Postgres, Stripe faked.

These cover what the service unit tests can't: the row lock under real
concurrency, the event ledger's unique key, the 402 entitlement gate and
cross-tenant isolation.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import APIRouter, Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from core.billing import get_entitled_tenant
from core.db import async_session_factory
from core.tenant import Tenant
from integrations.stripe.fake import event_payload, sign
from integrations.stripe.port import SubscriptionInfo
from models.billing import StripeEvent, Subscription


def _auth(tenant) -> dict[str, str]:
    return {"Authorization": f"Bearer {tenant.token}"}


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def ledger():
    """Event ids used by a test; removed from `stripe_events` afterwards."""
    ids: list[str] = []
    yield ids
    async with async_session_factory() as session:
        await session.execute(delete(StripeEvent).where(StripeEvent.event_id.in_(ids)))
        await session.commit()


async def _row(business_id: uuid.UUID) -> Subscription | None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Subscription).where(Subscription.business_id == business_id)
        )
        return result.scalar_one_or_none()


def _sub(sub_id: str, customer_id: str, status: str) -> SubscriptionInfo:
    now = datetime.now(UTC)
    return SubscriptionInfo(
        id=sub_id,
        customer_id=customer_id,
        status=status,
        price_id="price_fake",
        trial_start=now if status == "trialing" else None,
        trial_end=now + timedelta(days=14) if status == "trialing" else None,
        current_period_end=now + timedelta(days=30),
        cancel_at_period_end=False,
    )


async def _subscribe(client, billing_gateway, tenant, choice: str = "trial") -> str:
    """Runs checkout -> Stripe completes it -> return-page sync. Returns the sub id."""
    response = await client.post(
        "/api/v1/billing/checkout", json={"choice": choice}, headers=_auth(tenant)
    )
    assert response.status_code == 200, response.text
    row = await _row(tenant.business_id)
    assert row is not None and row.stripe_customer_id and row.pending_checkout_session_id
    sub_id = billing_gateway.new_subscription_id()
    status = "trialing" if choice == "trial" else "active"
    billing_gateway.complete_checkout(
        row.pending_checkout_session_id, _sub(sub_id, row.stripe_customer_id, status)
    )
    sync = await client.post(
        f"/api/v1/billing/checkout/{row.pending_checkout_session_id}/sync", headers=_auth(tenant)
    )
    assert sync.status_code == 200, sync.text
    return sub_id


async def test_billing_endpoints_require_a_bearer_token(client):
    for method, path in [
        ("GET", "/api/v1/billing/plan"),
        ("GET", "/api/v1/billing/subscription"),
        ("POST", "/api/v1/billing/checkout"),
        ("POST", "/api/v1/billing/portal"),
    ]:
        response = await client.request(method, path, json={"choice": "trial"})
        assert response.status_code == 401, path


async def test_checkout_before_onboarding_is_404(client, mint_token):
    token = mint_token(subject=f"user_{uuid.uuid4().hex}")
    response = await client.post(
        "/api/v1/billing/checkout",
        json={"choice": "trial"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "business_not_found"


async def test_checkout_rejects_an_unknown_choice(client, two_tenants):
    tenant, _ = two_tenants
    response = await client.post(
        "/api/v1/billing/checkout", json={"choice": "free-forever"}, headers=_auth(tenant)
    )
    assert response.status_code == 422


async def test_me_reports_no_access_until_checkout_completes(client, two_tenants):
    tenant, _ = two_tenants

    before = await client.get("/api/v1/me", headers=_auth(tenant))
    await client.post("/api/v1/billing/checkout", json={"choice": "trial"}, headers=_auth(tenant))
    pending = await client.get("/api/v1/me", headers=_auth(tenant))

    assert before.json()["subscription"]["entitled"] is False
    # An open Checkout Session is not a subscription: no access yet.
    assert pending.json()["subscription"]["entitled"] is False
    assert pending.json()["subscription"]["status"] is None


async def test_trial_flow_end_to_end(client, billing_gateway, two_tenants):
    tenant, _ = two_tenants

    plan = await client.get("/api/v1/billing/plan", headers=_auth(tenant))
    await _subscribe(client, billing_gateway, tenant, "trial")
    me = await client.get("/api/v1/me", headers=_auth(tenant))
    portal = await client.post("/api/v1/billing/portal", headers=_auth(tenant))

    assert plan.json() == {
        "amount": 2900,
        "currency": "usd",
        "interval": "month",
        "trial_days": 14,
        "trial_available": True,
    }
    assert billing_gateway.checkout_calls[0].trial_days == 14
    subscription = me.json()["subscription"]
    assert subscription["entitled"] is True
    assert subscription["status"] == "trialing"
    assert subscription["trial_ends_at"] is not None
    assert subscription["trial_available"] is False
    assert portal.status_code == 200
    assert portal.json()["url"].startswith("https://billing.stripe.test/")


async def test_sync_rejects_a_malformed_session_id(client, two_tenants):
    tenant, _ = two_tenants
    response = await client.post(
        "/api/v1/billing/checkout/not-a-session/sync", headers=_auth(tenant)
    )
    assert response.status_code == 422


async def test_tenant_cannot_sync_another_tenants_checkout(client, two_tenants):
    tenant_a, tenant_b = two_tenants
    await client.post("/api/v1/billing/checkout", json={"choice": "paid"}, headers=_auth(tenant_a))
    await client.post("/api/v1/billing/checkout", json={"choice": "paid"}, headers=_auth(tenant_b))
    row_a = await _row(tenant_a.business_id)
    assert row_a is not None

    response = await client.post(
        f"/api/v1/billing/checkout/{row_a.pending_checkout_session_id}/sync",
        headers=_auth(tenant_b),
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "checkout_not_found"


async def test_one_tenants_subscription_never_shows_for_another(
    client, billing_gateway, two_tenants
):
    tenant_a, tenant_b = two_tenants
    await _subscribe(client, billing_gateway, tenant_a, "paid")

    me_b = await client.get("/api/v1/me", headers=_auth(tenant_b))
    portal_b = await client.post("/api/v1/billing/portal", headers=_auth(tenant_b))

    assert me_b.json()["subscription"]["entitled"] is False
    assert portal_b.status_code == 404


async def test_concurrent_checkouts_leave_one_open_session(client, billing_gateway, two_tenants):
    """Double-click / two tabs: the row lock serializes them."""
    tenant, _ = two_tenants

    responses = await asyncio.gather(
        *[
            client.post(
                "/api/v1/billing/checkout", json={"choice": "paid"}, headers=_auth(tenant)
            )
            for _ in range(3)
        ]
    )

    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    async with async_session_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(Subscription)
            .where(Subscription.business_id == tenant.business_id)
        )
    assert count == 1
    assert len(billing_gateway.checkout_calls) == 3
    assert len(billing_gateway.expired_sessions) == 2
    open_sessions = [s for s in billing_gateway.sessions.values() if s.status == "open"]
    assert len(open_sessions) == 1


async def test_active_subscriber_cannot_start_another_checkout(
    client, billing_gateway, two_tenants
):
    tenant, _ = two_tenants
    await _subscribe(client, billing_gateway, tenant, "paid")

    response = await client.post(
        "/api/v1/billing/checkout", json={"choice": "paid"}, headers=_auth(tenant)
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "subscription_active"


# Webhook


async def _post_webhook(client, payload: bytes, signature: str | None):
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["Stripe-Signature"] = signature
    return await client.post("/api/v1/webhooks/stripe", content=payload, headers=headers)


async def test_webhook_rejects_a_tampered_or_missing_signature(client):
    payload = event_payload(f"evt_{uuid.uuid4().hex}", "customer.subscription.updated", "s", "c")

    tampered = await _post_webhook(client, payload, sign(payload + b" "))
    missing = await _post_webhook(client, payload, None)

    assert tampered.status_code == 400
    assert missing.status_code == 400
    assert tampered.json()["error"]["code"] == "invalid_webhook"


async def test_webhook_cancellation_revokes_access_and_replays_are_noops(
    client, billing_gateway, two_tenants, ledger
):
    tenant, _ = two_tenants
    sub_id = await _subscribe(client, billing_gateway, tenant, "paid")
    row = await _row(tenant.business_id)
    assert row is not None and row.stripe_customer_id
    billing_gateway.subscriptions[sub_id] = _sub(sub_id, row.stripe_customer_id, "canceled")
    event_id = f"evt_{uuid.uuid4().hex}"
    ledger.append(event_id)
    customer_id = row.stripe_customer_id
    payload = event_payload(event_id, "customer.subscription.deleted", sub_id, customer_id)

    first = await _post_webhook(client, payload, sign(payload))
    # Stripe-side state changes again, then the same event is redelivered.
    billing_gateway.subscriptions[sub_id] = _sub(sub_id, row.stripe_customer_id, "active")
    replay = await _post_webhook(client, payload, sign(payload))
    me = await client.get("/api/v1/me", headers=_auth(tenant))

    assert first.status_code == 200
    assert replay.status_code == 200
    assert me.json()["subscription"]["status"] == "canceled"
    assert me.json()["subscription"]["entitled"] is False


# Entitlement gate


@pytest.fixture
def gated_app(app):
    probe = APIRouter()

    @probe.get("/api/v1/_probe")
    async def gated(tenant: Tenant = Depends(get_entitled_tenant)) -> dict[str, str]:
        assert tenant.business is not None
        return {"business_id": str(tenant.business.id)}

    app.include_router(probe)
    return app


async def test_entitlement_gate_is_enforced_on_the_server(gated_app, billing_gateway, two_tenants):
    tenant_a, tenant_b = two_tenants
    async with AsyncClient(transport=ASGITransport(app=gated_app), base_url="http://test") as c:
        await _subscribe(c, billing_gateway, tenant_a, "trial")

        allowed = await c.get("/api/v1/_probe", headers=_auth(tenant_a))
        refused = await c.get("/api/v1/_probe", headers=_auth(tenant_b))

    assert allowed.status_code == 200
    assert allowed.json()["business_id"] == str(tenant_a.business_id)
    assert refused.status_code == 402
    assert refused.json()["error"]["code"] == "subscription_required"


async def test_refused_checkout_keeps_the_sync_it_performed(client, billing_gateway, two_tenants):
    """Tab A paid, webhook not here yet, tab B clicks again: 409, and the DB knows."""
    tenant, _ = two_tenants
    await client.post("/api/v1/billing/checkout", json={"choice": "paid"}, headers=_auth(tenant))
    row = await _row(tenant.business_id)
    assert row is not None and row.stripe_customer_id and row.pending_checkout_session_id
    billing_gateway.complete_checkout(
        row.pending_checkout_session_id,
        _sub(billing_gateway.new_subscription_id(), row.stripe_customer_id, "active"),
    )

    response = await client.post(
        "/api/v1/billing/checkout", json={"choice": "paid"}, headers=_auth(tenant)
    )
    after = await _row(tenant.business_id)

    assert response.status_code == 409
    assert after is not None
    assert after.status == "active"
    assert after.pending_checkout_session_id is None


async def test_checkout_while_the_row_is_locked_is_a_clean_409(client, two_tenants):
    tenant, _ = two_tenants
    await client.post("/api/v1/billing/checkout", json={"choice": "paid"}, headers=_auth(tenant))

    async with async_session_factory() as holder:
        await holder.execute(
            select(Subscription)
            .where(Subscription.business_id == tenant.business_id)
            .with_for_update()
        )
        response = await client.post(
            "/api/v1/billing/checkout", json={"choice": "paid"}, headers=_auth(tenant)
        )
        await holder.rollback()

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "billing_busy"
