"""`/api/v1/billing` (owner-facing) and `/api/v1/webhooks/stripe` (Stripe-facing).

Router responsibility only: parse, call one `services.billing` function,
commit, shape the response.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.billing import get_billing_config, get_billing_gateway, get_billing_store
from core.db import get_session
from core.tenant import Tenant, get_tenant
from integrations.stripe import BillingGateway
from repositories.billing import SqlBillingStore
from schemas.billing import (
    CHECKOUT_SESSION_ID_PATTERN,
    CheckoutCreate,
    PlanRead,
    RedirectRead,
    SubscriptionRead,
    WebhookAck,
)
from services import billing
from services.billing import BillingConfig
from services.business import BusinessNotFoundError

logger = logging.getLogger("tether.billing")

router = APIRouter(tags=["billing"])

Store = Annotated[SqlBillingStore, Depends(get_billing_store)]
Gateway = Annotated[BillingGateway, Depends(get_billing_gateway)]
Config = Annotated[BillingConfig, Depends(get_billing_config)]


@router.get("/billing/plan", response_model=PlanRead)
async def get_plan(
    store: Store, gateway: Gateway, config: Config, tenant: Tenant = Depends(get_tenant)
) -> PlanRead:
    return PlanRead.model_validate(await billing.get_plan(store, gateway, config, tenant))


@router.get("/billing/subscription", response_model=SubscriptionRead)
async def get_subscription(store: Store, tenant: Tenant = Depends(get_tenant)) -> SubscriptionRead:
    access = await billing.summarize(store, tenant)
    if access is None:
        raise BusinessNotFoundError("You haven't created a business yet.")
    return SubscriptionRead.model_validate(access)


@router.post("/billing/checkout", response_model=RedirectRead)
async def post_checkout(
    body: CheckoutCreate,
    store: Store,
    gateway: Gateway,
    config: Config,
    tenant: Tenant = Depends(get_tenant),
    session: AsyncSession = Depends(get_session),
) -> RedirectRead:
    try:
        url = await billing.start_checkout(store, gateway, config, tenant, body.choice)
    except (billing.SubscriptionActiveError, billing.TrialAlreadyUsedError):
        # The refusal may come after syncing a completed earlier checkout or
        # expiring an open one; keep that state rather than rolling it back.
        await session.commit()
        raise
    await session.commit()
    return RedirectRead(url=url)


@router.post("/billing/checkout/{session_id}/sync", response_model=SubscriptionRead)
async def post_checkout_sync(
    session_id: Annotated[str, Path(pattern=CHECKOUT_SESSION_ID_PATTERN)],
    store: Store,
    gateway: Gateway,
    tenant: Tenant = Depends(get_tenant),
    session: AsyncSession = Depends(get_session),
) -> SubscriptionRead:
    access = await billing.sync_checkout(store, gateway, tenant, session_id)
    await session.commit()
    return SubscriptionRead.model_validate(access)


@router.post("/billing/portal", response_model=RedirectRead)
async def post_portal(
    store: Store, gateway: Gateway, config: Config, tenant: Tenant = Depends(get_tenant)
) -> RedirectRead:
    return RedirectRead(url=await billing.open_portal(store, gateway, config, tenant))


@router.post("/webhooks/stripe", response_model=WebhookAck, include_in_schema=False)
async def post_stripe_webhook(
    request: Request,
    store: Store,
    gateway: Gateway,
    session: AsyncSession = Depends(get_session),
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> WebhookAck:
    # Raw bytes: the signature is over the exact payload, never re-serialized JSON.
    payload = await request.body()
    outcome = await billing.apply_event(store, gateway, payload, stripe_signature)
    await session.commit()
    logger.info("Stripe webhook processed: %s", outcome)
    return WebhookAck()
