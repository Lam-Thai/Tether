"""Real adapter: the `BillingGateway` port over the official Stripe SDK.

Bounded retries come from the SDK (`max_network_retries`, which also
attaches idempotency keys to retried POSTs); every call is additionally
capped by a wall-clock timeout so a slow Stripe can't hang a request.
"""

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any

import stripe

from integrations.stripe.port import (
    CheckoutSessionInfo,
    GatewayNotFoundError,
    InvalidWebhookSignatureError,
    PriceInfo,
    SubscriptionInfo,
    WebhookEvent,
)

MAX_NETWORK_RETRIES = 2
CALL_TIMEOUT_SECONDS = 15


def _ts(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value, tz=UTC) if value else None


def _id(value: Any) -> str | None:
    """Stripe fields like `customer` are either an id or an expanded object."""
    if value is None or isinstance(value, str):
        return value
    return value.id


class StripeBillingGateway:
    def __init__(self, *, api_key: str, webhook_secret: str) -> None:
        self._client = stripe.StripeClient(api_key, max_network_retries=MAX_NETWORK_RETRIES)
        self._webhook_secret = webhook_secret

    async def _call[T](self, awaitable: Awaitable[T]) -> T:
        try:
            async with asyncio.timeout(CALL_TIMEOUT_SECONDS):
                return await awaitable
        except stripe.InvalidRequestError as exc:
            if exc.code == "resource_missing":
                raise GatewayNotFoundError(str(exc.user_message or exc)) from exc
            raise

    async def create_customer(self, *, business_id: uuid.UUID, email: str, name: str) -> str:
        metadata = {"business_id": str(business_id)}
        # Same business + same details -> same customer, even across concurrent
        # first checkouts. The digest keeps a retry after a rename from reusing
        # the key with different params (which Stripe rejects).
        digest = hashlib.sha256(f"{email}|{name}".encode()).hexdigest()[:16]
        customer = await self._call(
            self._client.v1.customers.create_async(
                params={"email": email, "name": name, "metadata": metadata},
                options={"idempotency_key": f"tether-customer-{business_id}-{digest}"},
            )
        )
        return customer.id

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
        metadata = {"business_id": str(business_id)}
        subscription_data: dict[str, Any] = {"metadata": metadata}
        params: dict[str, Any] = {
            "mode": "subscription",
            "customer": customer_id,
            "client_reference_id": str(business_id),
            "line_items": [{"price": price_id, "quantity": 1}],
            "metadata": metadata,
            "success_url": success_url,
            "cancel_url": cancel_url,
        }
        if trial_days is not None:
            subscription_data["trial_period_days"] = trial_days
            # No card up front; the trial cancels itself if none is added.
            subscription_data["trial_settings"] = {
                "end_behavior": {"missing_payment_method": "cancel"}
            }
            params["payment_method_collection"] = "if_required"
        params["subscription_data"] = subscription_data

        session = await self._call(self._client.v1.checkout.sessions.create_async(params=params))  # type: ignore[arg-type]
        return self._to_checkout(session)

    async def retrieve_checkout_session(self, session_id: str) -> CheckoutSessionInfo:
        session = await self._call(self._client.v1.checkout.sessions.retrieve_async(session_id))
        return self._to_checkout(session)

    async def expire_checkout_session(self, session_id: str) -> None:
        await self._call(self._client.v1.checkout.sessions.expire_async(session_id))

    async def retrieve_subscription(self, subscription_id: str) -> SubscriptionInfo:
        sub = await self._call(self._client.v1.subscriptions.retrieve_async(subscription_id))
        items = sub["items"].data
        first_item = items[0] if items else None
        return SubscriptionInfo(
            id=sub.id,
            customer_id=_id(sub.customer) or "",
            status=sub.status,
            price_id=first_item.price.id if first_item else None,
            trial_start=_ts(sub.trial_start),
            trial_end=_ts(sub.trial_end),
            # Since API 2025-03-31 the billing period lives on subscription items.
            current_period_end=_ts(first_item.current_period_end) if first_item else None,
            cancel_at_period_end=bool(sub.cancel_at_period_end),
        )

    async def retrieve_price(self, price_id: str) -> PriceInfo:
        price = await self._call(self._client.v1.prices.retrieve_async(price_id))
        return PriceInfo(
            id=price.id,
            unit_amount=price.unit_amount or 0,
            currency=price.currency,
            interval=price.recurring.interval if price.recurring else "one_time",
        )

    async def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        portal = await self._call(
            self._client.v1.billing_portal.sessions.create_async(
                params={"customer": customer_id, "return_url": return_url}
            )
        )
        return portal.url

    def parse_webhook(self, payload: bytes, signature: str | None) -> WebhookEvent:
        if not signature:
            raise InvalidWebhookSignatureError("Missing Stripe-Signature header.")
        try:
            event = self._client.construct_event(payload, signature, self._webhook_secret)
        except (stripe.SignatureVerificationError, ValueError) as exc:
            raise InvalidWebhookSignatureError("Webhook signature did not verify.") from exc
        obj = event.data.object
        return WebhookEvent(
            id=event.id,
            type=event.type,
            object_id=obj["id"],
            # StripeObject isn't a dict in stripe-python 16: no `.get`.
            customer_id=_id(getattr(obj, "customer", None)),
        )

    @staticmethod
    def _to_checkout(session: Any) -> CheckoutSessionInfo:
        return CheckoutSessionInfo(
            id=session.id,
            url=session.url,
            status=session.status or "open",
            customer_id=_id(session.customer),
            subscription_id=_id(session.subscription),
        )
