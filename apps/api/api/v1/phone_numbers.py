"""`/api/v1/phone-numbers`: the owner associates a Twilio number. Router only.

Every route depends on `get_entitled_tenant`; the business always comes from the tenant,
so another business's number is a 404 and its existence is never revealed. There is no
purchase route: Tether never buys numbers.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.billing import get_entitled_tenant
from core.db import get_session
from core.tenant import Tenant
from core.voice import get_phone_number_service
from schemas.phone_number import (
    AvailableNumberRead,
    PhoneNumberAssociate,
    PhoneNumberDisconnectRead,
    PhoneNumberRead,
    PhoneNumbersRead,
)
from services.phone_numbers import PhoneNumberService

router = APIRouter(tags=["phone-numbers"])

Entitled = Annotated[Tenant, Depends(get_entitled_tenant)]
Numbers = Annotated[PhoneNumberService, Depends(get_phone_number_service)]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/phone-numbers", response_model=PhoneNumbersRead)
async def list_phone_numbers(tenant: Entitled, numbers: Numbers) -> PhoneNumbersRead:
    overview = await numbers.overview(tenant)
    return PhoneNumbersRead(
        configured=overview.configured,
        trial=overview.trial,
        numbers=[PhoneNumberRead.model_validate(n) for n in overview.numbers],
        available=[AvailableNumberRead.model_validate(n) for n in overview.available],
        available_error=overview.available_error,  # type: ignore[arg-type]
    )


@router.post("/phone-numbers", response_model=PhoneNumberRead, status_code=201)
async def associate_phone_number(
    body: PhoneNumberAssociate, tenant: Entitled, numbers: Numbers, session: Session
) -> PhoneNumberRead:
    number = await numbers.associate(tenant, body.provider_number_sid)
    await session.commit()
    return PhoneNumberRead.model_validate(number)


@router.post("/phone-numbers/{number_id}/test", response_model=PhoneNumberRead)
async def test_phone_number(
    number_id: UUID, tenant: Entitled, numbers: Numbers, session: Session
) -> PhoneNumberRead:
    number = await numbers.test(tenant, number_id)
    await session.commit()
    return PhoneNumberRead.model_validate(number)


@router.delete("/phone-numbers/{number_id}", response_model=PhoneNumberDisconnectRead)
async def disconnect_phone_number(
    number_id: UUID, tenant: Entitled, numbers: Numbers, session: Session
) -> PhoneNumberDisconnectRead:
    cleared = await numbers.disconnect(tenant, number_id)
    await session.commit()
    return PhoneNumberDisconnectRead(webhooks_cleared=cleared)
