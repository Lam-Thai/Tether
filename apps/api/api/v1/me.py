"""`GET /api/v1/me`: the caller, their business and its billing access.

One call is enough for the web app to route: no business -> onboarding,
not entitled -> plan selection, entitled -> dashboard.
"""

from fastapi import APIRouter, Depends

from core.billing import get_billing_store
from core.tenant import Tenant, get_tenant
from repositories.billing import SqlBillingStore
from schemas.billing import SubscriptionRead
from schemas.business import BusinessRead
from schemas.me import MeRead
from services.billing import summarize

router = APIRouter(tags=["me"])


@router.get("/me", response_model=MeRead)
async def get_me(
    tenant: Tenant = Depends(get_tenant),
    store: SqlBillingStore = Depends(get_billing_store),
) -> MeRead:
    business = BusinessRead.model_validate(tenant.business) if tenant.business else None
    access = await summarize(store, tenant)
    subscription = SubscriptionRead.model_validate(access) if access else None
    return MeRead(
        id=tenant.user.id, email=tenant.user.email, business=business, subscription=subscription
    )
