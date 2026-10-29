"""The billing port: everything `services/billing.py` may ask of Stripe.

Plain dataclasses in, plain dataclasses out, so no Stripe SDK type ever
leaves `integrations/stripe/`. `StripeBillingGateway` is the real adapter;
`FakeBillingGateway` is the in-memory stand-in for tests.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class GatewayNotFoundError(Exception):
    """The Stripe object doesn't exist (or isn't visible to this account)."""


class InvalidWebhookSignatureError(Exception):
    """A webhook payload's signature is missing, stale or doesn't verify."""


@dataclass(frozen=True)
class CheckoutSessionInfo:
    id: str
    url: str | None
    status: str  # "open" | "complete" | "expired"
    customer_id: str | None
    subscription_id: str | None


@dataclass(frozen=True)
class SubscriptionInfo:
    id: str
    customer_id: str
    status: str  # Stripe subscription status, e.g. "trialing", "active", "canceled"
    price_id: str | None
    trial_start: datetime | None
    trial_end: datetime | None
    current_period_end: datetime | None
    cancel_at_period_end: bool


@dataclass(frozen=True)
class PriceInfo:
    id: str
    unit_amount: int  # minor units, e.g. cents
    currency: str
    interval: str  # "month" | "year" | ...


@dataclass(frozen=True)
class WebhookEvent:
    id: str
    type: str
    object_id: str
    customer_id: str | None


class BillingGateway(Protocol):
    async def create_customer(self, *, business_id: uuid.UUID, email: str, name: str) -> str: ...

    async def create_checkout_session(
        self,
        *,
        customer_id: str,
        price_id: str,
        business_id: uuid.UUID,
        trial_days: int | None,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSessionInfo: ...

    async def retrieve_checkout_session(self, session_id: str) -> CheckoutSessionInfo: ...

    async def expire_checkout_session(self, session_id: str) -> None: ...

    async def retrieve_subscription(self, subscription_id: str) -> SubscriptionInfo: ...

    async def retrieve_price(self, price_id: str) -> PriceInfo: ...

    async def delete_customer(self, *, customer_id: str, business_id: uuid.UUID) -> None:
        """Deletes the Stripe customer: its subscriptions end at once (no proration) and
        saved cards are removed. `GatewayNotFoundError` when it is already gone."""
        ...

    async def create_portal_session(self, *, customer_id: str, return_url: str) -> str: ...

    def parse_webhook(self, payload: bytes, signature: str | None) -> WebhookEvent: ...
