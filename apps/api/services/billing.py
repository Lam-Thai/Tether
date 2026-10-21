"""Billing: the deep module behind `/api/v1/billing` and the Stripe webhook.

Small surface (`start_checkout`, `sync_checkout`, `apply_event`,
`open_portal`, `get_plan`, `summarize`, `access_for`) over the rules that
keep money correct:

- Stripe is the source of truth. Webhooks and the return-page sync both
  re-fetch the subscription from Stripe rather than trusting a payload.
- Rows are locked per business (or per customer on the webhook path), so
  concurrent checkouts and out-of-order webhooks can't write stale state.
- A business has at most one open Checkout Session: starting a new one
  expires the previous, so two tabs can't create two subscriptions.
- `business_id` only ever comes from `Tenant` or from a Stripe customer we
  created and mapped ourselves.

No FastAPI or SQLAlchemy imports: the router passes a `BillingStore`
(repositories bound to the request's session) and a `BillingGateway`.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from core.errors import AppError
from core.tenant import Tenant
from integrations.stripe.port import (
    BillingGateway,
    GatewayNotFoundError,
    InvalidWebhookSignatureError,
    SubscriptionInfo,
)
from models.billing import Subscription
from services.business import BusinessNotFoundError

logger = logging.getLogger("tether.billing")

PlanChoice = Literal["trial", "paid"]
EventOutcome = Literal["applied", "duplicate", "ignored"]

# `past_due` stays entitled while Stripe retries the card (the UI shows a banner).
ENTITLED_STATUSES = frozenset({"trialing", "active", "past_due"})
HANDLED_EVENTS = frozenset(
    {
        "checkout.session.completed",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }
)


class SubscriptionRequiredError(AppError):
    status_code = 402
    code = "subscription_required"


class SubscriptionActiveError(AppError):
    status_code = 409
    code = "subscription_active"


class TrialAlreadyUsedError(AppError):
    status_code = 409
    code = "trial_already_used"


class CheckoutNotFoundError(AppError):
    status_code = 404
    code = "checkout_not_found"


class BillingAccountNotFoundError(AppError):
    status_code = 404
    code = "billing_account_not_found"


class InvalidWebhookError(AppError):
    status_code = 400
    code = "invalid_webhook"


class BillingStore(Protocol):
    async def get_for_business(self, business_id: uuid.UUID) -> Subscription | None: ...
    async def lock_for_business(self, business_id: uuid.UUID) -> Subscription: ...
    async def lock_for_customer(self, customer_id: str) -> Subscription | None: ...
    async def record_event(self, event_id: str, event_type: str) -> bool: ...
    async def flush(self) -> None: ...


@dataclass(frozen=True)
class BillingConfig:
    price_id: str
    trial_days: int
    app_base_url: str


@dataclass(frozen=True)
class Access:
    entitled: bool
    status: str | None
    trial_ends_at: datetime | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    trial_available: bool


@dataclass(frozen=True)
class Plan:
    amount: int
    currency: str
    interval: str
    trial_days: int
    trial_available: bool


def access_for(subscription: Subscription | None) -> Access:
    if subscription is None:
        return Access(False, None, None, None, False, trial_available=True)
    return Access(
        entitled=subscription.status in ENTITLED_STATUSES,
        status=subscription.status,
        trial_ends_at=subscription.trial_end if subscription.status == "trialing" else None,
        current_period_end=subscription.current_period_end,
        cancel_at_period_end=subscription.cancel_at_period_end,
        trial_available=not _trial_used(subscription),
    )


def _trial_used(subscription: Subscription) -> bool:
    # The trial is for new customers: any past subscription rules it out.
    return (
        subscription.trial_started_at is not None or subscription.stripe_subscription_id is not None
    )


def _require_business(tenant: Tenant) -> uuid.UUID:
    if tenant.business is None:
        raise BusinessNotFoundError("Create your business before choosing a plan.")
    return tenant.business.id


def _apply_subscription(row: Subscription, info: SubscriptionInfo) -> None:
    if info.customer_id != row.stripe_customer_id:
        logger.warning("Subscription %s belongs to another customer; ignored", info.id)
        return
    replacing = row.stripe_subscription_id not in (None, info.id)
    if replacing and row.status in ENTITLED_STATUSES:
        if info.status not in ENTITLED_STATUSES:
            # A stale event about an older, dead subscription must not demote the live one.
            return
        # Two live subscriptions for one business means double billing; surface it.
        logger.warning(
            "Business %s has two live subscriptions (%s, %s); tracking the newer one",
            row.business_id,
            row.stripe_subscription_id,
            info.id,
        )
    row.stripe_subscription_id = info.id
    row.status = info.status
    row.price_id = info.price_id
    row.trial_end = info.trial_end
    row.current_period_end = info.current_period_end
    row.cancel_at_period_end = info.cancel_at_period_end
    if info.trial_start is not None and row.trial_started_at is None:
        row.trial_started_at = info.trial_start


async def _sync_from_session(row: Subscription, gateway: BillingGateway, session_id: str) -> None:
    session = await gateway.retrieve_checkout_session(session_id)
    if session.status == "complete" and session.subscription_id:
        _apply_subscription(row, await gateway.retrieve_subscription(session.subscription_id))
    if row.pending_checkout_session_id == session_id and session.status != "open":
        row.pending_checkout_session_id = None


async def get_plan(
    store: BillingStore, gateway: BillingGateway, config: BillingConfig, tenant: Tenant
) -> Plan:
    business_id = _require_business(tenant)
    price = await gateway.retrieve_price(config.price_id)
    return Plan(
        amount=price.unit_amount,
        currency=price.currency,
        interval=price.interval,
        trial_days=config.trial_days,
        trial_available=access_for(await store.get_for_business(business_id)).trial_available,
    )


async def summarize(store: BillingStore, tenant: Tenant) -> Access | None:
    if tenant.business is None:
        return None
    return access_for(await store.get_for_business(tenant.business.id))


async def start_checkout(
    store: BillingStore,
    gateway: BillingGateway,
    config: BillingConfig,
    tenant: Tenant,
    choice: PlanChoice,
) -> str:
    """Returns the Stripe-hosted Checkout URL to redirect the owner to."""
    business_id = _require_business(tenant)
    assert tenant.business is not None
    row = await store.lock_for_business(business_id)

    if row.pending_checkout_session_id:
        previous = row.pending_checkout_session_id
        try:
            await _sync_from_session(row, gateway, previous)
            if row.pending_checkout_session_id == previous:
                await gateway.expire_checkout_session(previous)
        except GatewayNotFoundError:
            pass
        row.pending_checkout_session_id = None

    if access_for(row).entitled:
        raise SubscriptionActiveError("This business already has an active plan.")
    if choice == "trial" and _trial_used(row):
        raise TrialAlreadyUsedError("The free trial has already been used for this business.")

    if row.stripe_customer_id is None:
        row.stripe_customer_id = await gateway.create_customer(
            business_id=business_id, email=tenant.user.email, name=tenant.business.name
        )

    session = await gateway.create_checkout_session(
        customer_id=row.stripe_customer_id,
        price_id=config.price_id,
        business_id=business_id,
        trial_days=config.trial_days if choice == "trial" else None,
        success_url=f"{config.app_base_url}/billing/return?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{config.app_base_url}/billing/plan?canceled=1",
    )
    if not session.url:
        raise RuntimeError("Stripe returned a Checkout Session without a URL.")
    row.pending_checkout_session_id = session.id
    await store.flush()
    return session.url


async def sync_checkout(
    store: BillingStore, gateway: BillingGateway, tenant: Tenant, session_id: str
) -> Access:
    """Called by the return page so it doesn't have to wait for the webhook."""
    business_id = _require_business(tenant)
    row = await store.lock_for_business(business_id)
    try:
        session = await gateway.retrieve_checkout_session(session_id)
    except GatewayNotFoundError:
        raise CheckoutNotFoundError("Checkout session not found.") from None
    # Same 404 for "doesn't exist" and "belongs to another business".
    if row.stripe_customer_id is None or session.customer_id != row.stripe_customer_id:
        raise CheckoutNotFoundError("Checkout session not found.")
    await _sync_from_session(row, gateway, session_id)
    await store.flush()
    return access_for(row)


async def apply_event(
    store: BillingStore, gateway: BillingGateway, payload: bytes, signature: str | None
) -> EventOutcome:
    try:
        event = gateway.parse_webhook(payload, signature)
    except InvalidWebhookSignatureError:
        raise InvalidWebhookError("Webhook signature did not verify.") from None

    if event.type not in HANDLED_EVENTS or event.customer_id is None:
        return "ignored"

    row = await store.lock_for_customer(event.customer_id)
    if row is None:
        # Not a customer Tether created (another integration on the account).
        # Not recorded, so a redelivery is still processed if the mapping lands later.
        logger.warning("Stripe event %s for unknown customer; ignored", event.id)
        return "ignored"
    # Recorded under the row lock, in the same transaction as the state change.
    if not await store.record_event(event.id, event.type):
        return "duplicate"

    if event.type == "checkout.session.completed":
        await _sync_from_session(row, gateway, event.object_id)
    else:
        _apply_subscription(row, await gateway.retrieve_subscription(event.object_id))
    await store.flush()
    return "applied"


async def open_portal(
    store: BillingStore, gateway: BillingGateway, config: BillingConfig, tenant: Tenant
) -> str:
    row = await store.get_for_business(_require_business(tenant))
    if row is None or row.stripe_customer_id is None:
        raise BillingAccountNotFoundError("There is no billing account for this business yet.")
    return await gateway.create_portal_session(
        customer_id=row.stripe_customer_id, return_url=f"{config.app_base_url}/dashboard"
    )
