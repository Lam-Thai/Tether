"""Every Stripe call through `MeteredBillingGateway` is metered; none is ever capped."""

import uuid

import pytest

from integrations.stripe.fake import FakeBillingGateway
from integrations.stripe.port import GatewayNotFoundError, InvalidWebhookSignatureError
from services.billing_metering import MeteredBillingGateway
from services.usage import UsageKind
from tests.services.memory_usage_store import MemoryUsageStore, guard


async def test_calls_are_metered_attributed_and_failures_counted():
    store = MemoryUsageStore()
    gateway = MeteredBillingGateway(FakeBillingGateway(), guard(store))
    business_id = uuid.uuid4()

    await gateway.create_customer(business_id=business_id, email="o@example.com", name="Alpha")
    await gateway.retrieve_price("price_fake")
    with pytest.raises(GatewayNotFoundError):
        await gateway.retrieve_subscription("sub_missing")

    assert store.count(UsageKind.BILLING_CALL) == 3  # global scope sees every call
    assert store.count(UsageKind.BILLING_CALL, business_id) == 1  # only the one with an id
    assert store.count(UsageKind.FAILED_REQUEST) == 1


async def test_webhook_parsing_is_local_and_not_metered():
    store = MemoryUsageStore()
    gateway = MeteredBillingGateway(FakeBillingGateway(), guard(store))
    with pytest.raises(InvalidWebhookSignatureError):
        gateway.parse_webhook(b"{}", None)
    assert store.events == []
