"""Billing dependencies, including the entitlement gate.

`get_entitled_tenant` is what every business-feature router depends on
(Slice 4 onwards) instead of `get_tenant`: no trialing/active/past_due
subscription means 402, checked on the server, not just hidden in the UI.
Onboarding, `/me`, `/business` and `/billing` stay on `get_tenant`, since
they're needed before a subscription exists.
"""

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.db import get_session
from core.tenant import Tenant, get_tenant
from integrations.stripe import BillingGateway, StripeBillingGateway
from repositories.billing import SqlBillingStore
from services.billing import BillingConfig, SubscriptionRequiredError, access_for
from services.business import BusinessNotFoundError

# One client per process; tests override `get_billing_gateway`.
_gateway = StripeBillingGateway(
    api_key=settings.stripe_secret_key, webhook_secret=settings.stripe_webhook_secret
)


def get_billing_gateway() -> BillingGateway:
    return _gateway


def get_billing_config() -> BillingConfig:
    return BillingConfig(
        price_id=settings.stripe_price_id,
        trial_days=settings.billing_trial_days,
        app_base_url=settings.app_base_url,
    )


def get_billing_store(session: AsyncSession = Depends(get_session)) -> SqlBillingStore:
    return SqlBillingStore(session)


async def get_entitled_tenant(
    tenant: Tenant = Depends(get_tenant),
    store: SqlBillingStore = Depends(get_billing_store),
) -> Tenant:
    if tenant.business is None:
        raise BusinessNotFoundError("You haven't created a business yet.")
    if not access_for(await store.get_for_business(tenant.business.id)).entitled:
        raise SubscriptionRequiredError("An active plan or free trial is required.")
    return tenant
