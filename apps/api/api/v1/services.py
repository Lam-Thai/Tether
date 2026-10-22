"""`/api/v1/services`: the owner's service catalog. Router only: parse, call one service
function, commit, shape. Every route needs an entitled tenant; the business always
comes from the tenant, never from the request."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.billing import get_entitled_tenant
from core.db import get_session
from core.directory import SqlDirectoryStore, get_directory_store, get_service_max
from core.tenant import Tenant
from schemas.service import ServiceCreate, ServiceList, ServiceRead, ServiceUpdate
from services import catalog

router = APIRouter(tags=["services"])

Entitled = Annotated[Tenant, Depends(get_entitled_tenant)]
Store = Annotated[SqlDirectoryStore, Depends(get_directory_store)]
MaxServices = Annotated[int, Depends(get_service_max)]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/services", response_model=ServiceList)
async def list_services(tenant: Entitled, store: Store, max_services: MaxServices) -> ServiceList:
    result = await catalog.get_catalog(store, tenant, max_services=max_services)
    return ServiceList(
        services=[ServiceRead.model_validate(s) for s in result.services],
        currency=result.currency,
        max_services=result.max_services,
    )


@router.post("/services", response_model=ServiceRead, status_code=status.HTTP_201_CREATED)
async def create_service(
    body: ServiceCreate, tenant: Entitled, store: Store, max_services: MaxServices, session: Session
) -> ServiceRead:
    service = await catalog.create_service(
        store, tenant, max_services=max_services, **body.model_dump()
    )
    await session.commit()
    return ServiceRead.model_validate(service)


@router.patch("/services/{service_id}", response_model=ServiceRead)
async def patch_service(
    service_id: UUID, body: ServiceUpdate, tenant: Entitled, store: Store, session: Session
) -> ServiceRead:
    service = await catalog.update_service(
        store, tenant, service_id, **body.model_dump(exclude_unset=True)
    )
    await session.commit()
    return ServiceRead.model_validate(service)


@router.delete("/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_service(
    service_id: UUID, tenant: Entitled, store: Store, session: Session
) -> Response:
    await catalog.delete_service(store, tenant, service_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
