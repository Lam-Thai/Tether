"""The real Stripe adapter.

Webhook parsing runs through the SDK's own signature verification with a
payload signed the way Stripe signs it (no network). The `live` test hits
Stripe test mode and only runs with STRIPE_LIVE_TESTS=1 plus real test keys.
"""

import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import UTC, datetime

import pytest
import stripe

from core.config import settings
from integrations.stripe.adapter import StripeBillingGateway
from integrations.stripe.port import InvalidWebhookSignatureError

SECRET = "whsec_adapter_test"


def _stripe_signature(payload: bytes, secret: str = SECRET, timestamp: int | None = None) -> str:
    t = timestamp or int(time.time())
    digest = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={digest}"


def _event(event_type: str, obj: dict) -> bytes:
    return json.dumps(
        {"id": "evt_123", "object": "event", "type": event_type, "data": {"object": obj}}
    ).encode()


@pytest.fixture
def gateway() -> StripeBillingGateway:
    return StripeBillingGateway(api_key="sk_test_placeholder", webhook_secret=SECRET)


def test_parse_webhook_maps_a_verified_event(gateway):
    payload = _event(
        "customer.subscription.updated",
        {"id": "sub_123", "object": "subscription", "customer": "cus_123"},
    )

    event = gateway.parse_webhook(payload, _stripe_signature(payload))

    assert (event.id, event.type, event.object_id, event.customer_id) == (
        "evt_123",
        "customer.subscription.updated",
        "sub_123",
        "cus_123",
    )


def test_parse_webhook_accepts_an_expanded_customer(gateway):
    payload = _event(
        "checkout.session.completed",
        {"id": "cs_test_1", "object": "checkout.session", "customer": {"id": "cus_9"}},
    )
    assert gateway.parse_webhook(payload, _stripe_signature(payload)).customer_id == "cus_9"


@pytest.mark.parametrize(
    "signature",
    [
        None,
        "",
        "t=1,v1=deadbeef",
        _stripe_signature(b"{}", secret="whsec_someone_else"),
    ],
    # Fixed ids: the last signature embeds the time, and parallel workers must agree.
    ids=["missing", "empty", "malformed", "wrong-secret"],
)
def test_parse_webhook_rejects_bad_signatures(gateway, signature):
    payload = _event("customer.subscription.updated", {"id": "sub_1", "customer": "cus_1"})
    with pytest.raises(InvalidWebhookSignatureError):
        gateway.parse_webhook(payload, signature)


def test_parse_webhook_rejects_a_replayed_old_signature(gateway):
    payload = _event("customer.subscription.updated", {"id": "sub_1", "customer": "cus_1"})
    stale = _stripe_signature(payload, timestamp=int(time.time()) - 3600)
    with pytest.raises(InvalidWebhookSignatureError):
        gateway.parse_webhook(payload, stale)


async def test_retrieve_subscription_maps_a_real_sdk_object(gateway, monkeypatch):
    """Period end comes from the first item (API 2025-03-31+), ids may be expanded."""
    sdk_sub = stripe.Subscription.construct_from(
        {
            "id": "sub_1",
            "object": "subscription",
            "customer": {"id": "cus_1", "object": "customer"},
            "status": "trialing",
            "trial_start": 1_760_000_000,
            "trial_end": 1_761_209_600,
            "cancel_at_period_end": True,
            "items": {
                "object": "list",
                "data": [
                    {
                        "id": "si_1",
                        "object": "subscription_item",
                        "current_period_end": 1_761_209_600,
                        "price": {"id": "price_1", "object": "price"},
                    }
                ],
            },
        },
        "sk_test_placeholder",
    )

    async def fake_retrieve(subscription_id, *args, **kwargs):
        assert subscription_id == "sub_1"
        return sdk_sub

    monkeypatch.setattr(gateway._client.v1.subscriptions, "retrieve_async", fake_retrieve)

    info = await gateway.retrieve_subscription("sub_1")

    assert info.customer_id == "cus_1"
    assert info.price_id == "price_1"
    assert info.status == "trialing"
    assert info.cancel_at_period_end is True
    assert info.trial_start == datetime.fromtimestamp(1_760_000_000, tz=UTC)
    assert info.current_period_end == datetime.fromtimestamp(1_761_209_600, tz=UTC)


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("STRIPE_LIVE_TESTS") != "1", reason="set STRIPE_LIVE_TESTS=1 to hit Stripe"
)
async def test_live_trial_checkout_session_in_test_mode():
    assert settings.stripe_secret_key.startswith("sk_test_"), "live test needs a test-mode key"
    live = StripeBillingGateway(
        api_key=settings.stripe_secret_key, webhook_secret=settings.stripe_webhook_secret
    )
    business_id = uuid.uuid4()

    price = await live.retrieve_price(settings.stripe_price_id)
    customer_id = await live.create_customer(
        business_id=business_id, email="live-test@example.com", name="Live Test"
    )
    session = await live.create_checkout_session(
        customer_id=customer_id,
        price_id=settings.stripe_price_id,
        business_id=business_id,
        trial_days=settings.billing_trial_days,
        success_url=f"{settings.app_base_url}/billing/return?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{settings.app_base_url}/billing/plan?canceled=1",
    )
    await live.expire_checkout_session(session.id)

    assert price.unit_amount > 0
    assert session.status == "open"
    assert session.url and session.url.startswith("https://checkout.stripe.com/")
    assert (await live.retrieve_checkout_session(session.id)).status == "expired"
