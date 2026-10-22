"""`/api/v1/business/hours`: the owner's weekly opening hours. Router only.

Registered before the `/business` router so the literal `hours` segment can never be
read as a `{business_id}` path parameter; the methods differ anyway (`PATCH
/business/{business_id}` vs `GET`/`PUT /business/hours`)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.billing import get_entitled_tenant
from core.db import get_session
from core.directory import SqlDirectoryStore, get_directory_store
from core.tenant import Tenant
from schemas.hours import DayHours, WeekHoursRead, WeekHoursUpdate
from services import hours as hours_service
from services.hours import WeekHours

router = APIRouter(tags=["hours"])

Entitled = Annotated[Tenant, Depends(get_entitled_tenant)]
Store = Annotated[SqlDirectoryStore, Depends(get_directory_store)]
Session = Annotated[AsyncSession, Depends(get_session)]


def _read(week: WeekHours) -> WeekHoursRead:
    return WeekHoursRead(
        timezone=week.timezone,
        configured=week.configured,
        days=[DayHours.model_validate(d) for d in week.days],
    )


@router.get("/business/hours", response_model=WeekHoursRead)
async def get_hours(tenant: Entitled, store: Store) -> WeekHoursRead:
    return _read(await hours_service.get_week(store, tenant))


@router.put("/business/hours", response_model=WeekHoursRead)
async def put_hours(
    body: WeekHoursUpdate, tenant: Entitled, store: Store, session: Session
) -> WeekHoursRead:
    week = await hours_service.replace_week(
        store,
        tenant,
        [
            hours_service.DayWindow(d.day_of_week, d.is_closed, d.opens_at, d.closes_at)
            for d in body.days
        ],
    )
    await session.commit()
    return _read(week)
