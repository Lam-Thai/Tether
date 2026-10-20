"""`GET /api/v1/me` — the authenticated caller plus their business, if any.

Router responsibility only: no business logic lives here.
"""

from fastapi import APIRouter, Depends

from core.tenant import Tenant, get_tenant
from schemas.business import BusinessRead
from schemas.me import MeRead

router = APIRouter(tags=["me"])


@router.get("/me", response_model=MeRead)
async def get_me(tenant: Tenant = Depends(get_tenant)) -> MeRead:
    business = BusinessRead.model_validate(tenant.business) if tenant.business else None
    return MeRead(id=tenant.user.id, email=tenant.user.email, business=business)
