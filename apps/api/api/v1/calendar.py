"""`/api/v1/calendar`: the owner connects a Google Calendar, picks which one Tether uses, and
previews the availability the agent would offer.

Router only: parse, call one `CalendarConnections` method, commit, shape. Every route
needs an entitled tenant; the business always comes from the tenant. The service
commits its own state changes that must survive a failing request (a revoked grant).
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.billing import get_entitled_tenant
from core.calendar import get_availability_service, get_calendar_connections
from core.db import get_session
from core.tenant import Tenant
from schemas.calendar import (
    AvailabilityRead,
    AvailabilityServiceRead,
    CalendarListRead,
    CalendarOptionRead,
    CalendarSelect,
    CalendarStatusRead,
    CallbackCreate,
    ConnectUrlRead,
    DisconnectRead,
    SlotRead,
)
from services.availability import (
    AvailabilityResult,
    AvailabilityService,
    CalendarUnavailable,
    NoAvailability,
    Slot,
    SlotPreference,
)
from services.business_tools import wall_time
from services.calendar import CalendarConnections, TenantIds

router = APIRouter(tags=["calendar"])

Entitled = Annotated[Tenant, Depends(get_entitled_tenant)]
Connections = Annotated[CalendarConnections, Depends(get_calendar_connections)]
Availability = Annotated[AvailabilityService, Depends(get_availability_service)]
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


@router.get("/calendar/availability", response_model=AvailabilityRead)
async def get_availability(
    service_id: UUID,
    date: date,
    tenant: Entitled,
    availability: Availability,
    session: Session,
) -> AvailabilityRead:
    """What the agent would see for this service and local date (the owner's preview)."""
    result = await availability.find_slots(tenant, service_id, SlotPreference(date))
    await session.commit()  # a revoked grant marks the connection reconnect_needed
    return _availability_read(result)


def _slot_read(slot: Slot) -> SlotRead:
    return SlotRead(start=slot.start, end=slot.end, label=wall_time(slot.start))


def _availability_read(result: AvailabilityResult) -> AvailabilityRead:
    common = {
        "date": result.date,
        "timezone": result.timezone,
        "service": AvailabilityServiceRead(
            id=result.service.id,
            name=result.service.name,
            duration_minutes=result.service.duration_minutes,
        ),
    }
    if isinstance(result, CalendarUnavailable):
        return AvailabilityRead(status="calendar_unavailable", reason=result.reason, **common)
    if isinstance(result, NoAvailability):
        return AvailabilityRead(status="no_availability", reason=result.reason, **common)
    return AvailabilityRead(
        status="available",
        slots=[_slot_read(slot) for slot in result.slots],
        offered=[_slot_read(slot) for slot in result.offered],
        **common,
    )
