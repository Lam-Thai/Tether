"""`/api/v1/business` — the caller's own business workspace.

Router responsibility only: parse the request, bind the session onto each
repository function, call one service function, shape the response. The
path carries `business_id` for PATCH (REST convention), but ownership is
always checked against `Tenant` — never trusted on its own.
"""
from functools import partial
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_session
from core.tenant import Tenant, get_tenant
from repositories.business import create_business as create_business_row
from repositories.business import get_business_by_id_for_owner
from repositories.business import update_business as update_business_row
from schemas.business import BusinessCreate, BusinessRead, BusinessUpdate
from services.business import create_business, get_current_business, update_business

router = APIRouter(tags=["business"])


@router.get("/business", response_model=BusinessRead)
async def get_business(tenant: Tenant = Depends(get_tenant)) -> BusinessRead:
    business = await get_current_business(tenant)
    return BusinessRead.model_validate(business)


@router.post("/business", response_model=BusinessRead, status_code=status.HTTP_201_CREATED)
async def post_business(
    body: BusinessCreate,
    tenant: Tenant = Depends(get_tenant),
    session: AsyncSession = Depends(get_session),
) -> BusinessRead:
    business = await create_business(
        partial(create_business_row, session),
        tenant,
        **body.model_dump(),
    )
    await session.commit()
    return BusinessRead.model_validate(business)


@router.patch("/business/{business_id}", response_model=BusinessRead)
async def patch_business(
    business_id: UUID,
    body: BusinessUpdate,
    tenant: Tenant = Depends(get_tenant),
    session: AsyncSession = Depends(get_session),
) -> BusinessRead:
    business = await update_business(
        partial(get_business_by_id_for_owner, session),
        partial(update_business_row, session),
        tenant,
        business_id,
        **body.model_dump(exclude_unset=True),
    )
    await session.commit()
    return BusinessRead.model_validate(business)
