"""`/api/v1/calendar`: the owner connects a Google Calendar and picks which one Tether uses.

Router only: parse, call one `CalendarConnections` method, commit, shape. Every route
needs an entitled tenant; the business always comes from the tenant. The service
commits its own state changes that must survive a failing request (a revoked grant).
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.billing import get_entitled_tenant
from core.calendar import get_calendar_connections
from core.db import get_session
from core.tenant import Tenant
from schemas.calendar import (
    CalendarListRead,
    CalendarOptionRead,
    CalendarSelect,
    CalendarStatusRead,
    CallbackCreate,
    ConnectUrlRead,
    DisconnectRead,
)
from services.calendar import CalendarConnections, TenantIds

router = APIRouter(tags=["calendar"])

Entitled = Annotated[Tenant, Depends(get_entitled_tenant)]
Connections = Annotated[CalendarConnections, Depends(get_calendar_connections)]
Session = Annotated[AsyncSession, Depends(get_session)]


def _ids(tenant: Tenant) -> TenantIds:
    assert tenant.business is not None  # get_entitled_tenant guarantees it
    return TenantIds(business_id=tenant.business.id, user_id=tenant.user.id)


@router.get("/calendar/connect", response_model=ConnectUrlRead)
async def get_connect_url(tenant: Entitled, connections: Connections) -> ConnectUrlRead:
    return ConnectUrlRead(url=connections.connect_url(_ids(tenant)))


@router.post("/calendar/callback", response_model=CalendarStatusRead)
async def post_callback(
    body: CallbackCreate, tenant: Entitled, connections: Connections, session: Session
) -> CalendarStatusRead:
    status = await connections.complete(_ids(tenant), code=body.code, state=body.state)
    await session.commit()
    return CalendarStatusRead.model_validate(status)


@router.get("/calendar/status", response_model=CalendarStatusRead)
async def get_status(
    tenant: Entitled, connections: Connections, session: Session
) -> CalendarStatusRead:
    status = await connections.status(_ids(tenant).business_id)
    await session.commit()
    return CalendarStatusRead.model_validate(status)


@router.get("/calendar/calendars", response_model=CalendarListRead)
async def get_calendars(
    tenant: Entitled, connections: Connections, session: Session
) -> CalendarListRead:
    calendars = await connections.calendars(_ids(tenant).business_id)
    await session.commit()
    return CalendarListRead(calendars=[CalendarOptionRead.model_validate(c) for c in calendars])


@router.patch("/calendar", response_model=CalendarStatusRead)
async def patch_calendar(
    body: CalendarSelect, tenant: Entitled, connections: Connections, session: Session
) -> CalendarStatusRead:
    status = await connections.select(_ids(tenant).business_id, body.calendar_id)
    await session.commit()
    return CalendarStatusRead.model_validate(status)


@router.delete("/calendar", response_model=DisconnectRead)
async def delete_calendar(
    tenant: Entitled, connections: Connections, session: Session
) -> DisconnectRead:
    result = await connections.disconnect(_ids(tenant).business_id)
    await session.commit()
    return DisconnectRead.model_validate(result)
