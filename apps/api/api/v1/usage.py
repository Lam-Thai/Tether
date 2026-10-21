"""`/api/v1/usage` — today's usage for the caller's business against its limits.

Router responsibility only. The business comes from the entitled tenant,
never from the request; the numbers come from `usage_counters` /
`usage_events` through `UsageGuard.summary`.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from core.billing import get_entitled_tenant
from core.tenant import Tenant
from core.usage import get_usage_guard
from schemas.usage import UsageRead
from services.agent import require_business
from services.usage import UsageGuard

router = APIRouter(tags=["usage"])

Entitled = Annotated[Tenant, Depends(get_entitled_tenant)]
Guard = Annotated[UsageGuard, Depends(get_usage_guard)]


@router.get("/usage", response_model=UsageRead)
async def get_usage(tenant: Entitled, usage: Guard) -> UsageRead:
    summary = await usage.summary(require_business(tenant).id)
    return UsageRead.model_validate(summary)
