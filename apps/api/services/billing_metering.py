"""`MeteredBillingGateway`: the `BillingGateway` port with every Stripe API call
going through `UsageGuard` (kind `billing_call`).

Metered only, never capped: no limit is configured for `billing_call`, so a
busy day can't block a checkout or a webhook. What it adds is the audit
trail and `failed_request` accounting the root `CLAUDE.md` asks of every
outbound provider call. Retries stay the SDK's own (bounded, with
idempotency keys on retried POSTs; see `integrations/stripe/adapter.py`).
`parse_webhook` is a local signature check, not a Stripe call, so it isn't
metered. Calls that carry a business id are attributed to it; the rest
(webhook-driven re-fetches) are metered globally.
"""

import uuid
from collections.abc import Awaitable, Callable

from integrations.stripe.port import (
    BillingGateway,
    CheckoutSessionInfo,
    PriceInfo,
    SubscriptionInfo,
    WebhookEvent,
)
from services.usage import UsageGuard, UsageKind


class MeteredBillingGateway:
    def __init__(self, inner: BillingGateway, usage: UsageGuard) -> None:
        self._inner = inner
        self._usage = usage

    async def _metered[T](
        self, call: Callable[[], Awaitable[T]], business_id: uuid.UUID | None = None
    ) -> T:
        async with self._usage.metered(UsageKind.BILLING_CALL, business_id=business_id):
            return await call()

    async def create_customer(self, *, business_id: uuid.UUID, email: str, name: str) -> str:
        return await self._metered(
            lambda: self._inner.create_customer(business_id=business_id, email=email, name=name),
            business_id,
        )

    async def create_checkout_session(
        self,
        *,
        customer_id: str,
        price_id: str,
        business_id: uuid.UUID,
        trial_days: int | None,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSessionInfo:
        return await self._metered(
            lambda: self._inner.create_checkout_session(
                customer_id=customer_id,
                price_id=price_id,
                business_id=business_id,
                trial_days=trial_days,
                success_url=success_url,
                cancel_url=cancel_url,
            ),
            business_id,
        )

    async def retrieve_checkout_session(self, session_id: str) -> CheckoutSessionInfo:
        return await self._metered(lambda: self._inner.retrieve_checkout_session(session_id))

    async def expire_checkout_session(self, session_id: str) -> None:
        await self._metered(lambda: self._inner.expire_checkout_session(session_id))

    async def retrieve_subscription(self, subscription_id: str) -> SubscriptionInfo:
        return await self._metered(lambda: self._inner.retrieve_subscription(subscription_id))

    async def retrieve_price(self, price_id: str) -> PriceInfo:
        return await self._metered(lambda: self._inner.retrieve_price(price_id))

    async def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        return await self._metered(
            lambda: self._inner.create_portal_session(
                customer_id=customer_id, return_url=return_url
            )
        )

    def parse_webhook(self, payload: bytes, signature: str | None) -> WebhookEvent:
        return self._inner.parse_webhook(payload, signature)
