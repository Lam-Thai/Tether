"""`/api/v1/business/booking-rules`: the owner's booking rules. Router only.

Registered before the `/business` router so the literal segment is never read as a
`{business_id}` path parameter."""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.billing import get_entitled_tenant
from core.booking_rules import SqlBookingRulesStore, default_booking_rules, get_booking_rules_store
from core.db import get_session
from core.tenant import Tenant
from schemas.booking_rules import BookingRulesRead, BookingRulesUpdate
from services import booking_rules as rules_service
from services.booking_rules import BookingRules, BookingRulesView

router = APIRouter(tags=["booking-rules"])

Entitled = Annotated[Tenant, Depends(get_entitled_tenant)]
Store = Annotated[SqlBookingRulesStore, Depends(get_booking_rules_store)]
Defaults = Annotated[BookingRules, Depends(default_booking_rules)]
Session = Annotated[AsyncSession, Depends(get_session)]


def _read(view: BookingRulesView) -> BookingRulesRead:
    return BookingRulesRead(**asdict(view.rules), customized=view.customized)


@router.get("/business/booking-rules", response_model=BookingRulesRead)
async def get_booking_rules(tenant: Entitled, store: Store, defaults: Defaults) -> BookingRulesRead:
    return _read(await rules_service.get_rules(store, tenant, defaults))


@router.put("/business/booking-rules", response_model=BookingRulesRead)
async def put_booking_rules(
    body: BookingRulesUpdate, tenant: Entitled, store: Store, session: Session
) -> BookingRulesRead:
    view = await rules_service.replace_rules(store, tenant, BookingRules(**body.model_dump()))
    await session.commit()
    return _read(view)
