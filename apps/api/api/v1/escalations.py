"""`/api/v1/escalations` and `/api/v1/business/escalation-settings`. Router only.

Every route depends on `get_entitled_tenant`; the business always comes from the
tenant, so another business's escalation is a 404. The settings routes are registered
before the `/business` router so the literal segment is never read as a `{business_id}`.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from core.billing import get_entitled_tenant
from core.config import settings as app_settings
from core.db import get_session
from core.escalation import get_escalation_service
from core.tenant import Tenant
from schemas.escalation import (
    EscalationListRead,
    EscalationRead,
    EscalationResolve,
    EscalationSettingsRead,
    EscalationSettingsUpdate,
)
from services.escalation import (
    EscalationRecord,
    EscalationService,
    EscalationSettings,
    EscalationSettingsView,
    StatusFilter,
)

router = APIRouter(tags=["escalations"])

Entitled = Annotated[Tenant, Depends(get_entitled_tenant)]
Escalations = Annotated[EscalationService, Depends(get_escalation_service)]
Session = Annotated[AsyncSession, Depends(get_session)]


def _read(record: EscalationRecord) -> EscalationRead:
    return EscalationRead.model_validate(record)


def _settings_read(view: EscalationSettingsView) -> EscalationSettingsRead:
    return EscalationSettingsRead(
        contact_phone=view.settings.contact_phone,
        sms_enabled=view.settings.sms_enabled,
        clarification_threshold=view.settings.clarification_threshold,
        urgent_keywords=list(view.settings.urgent_keywords),
        customized=view.customized,
        sms_configured=app_settings.twilio_configured,
    )


@router.get("/business/escalation-settings", response_model=EscalationSettingsRead)
async def get_escalation_settings(
    tenant: Entitled, escalations: Escalations
) -> EscalationSettingsRead:
    return _settings_read(await escalations.get_settings(tenant))


@router.put("/business/escalation-settings", response_model=EscalationSettingsRead)
async def put_escalation_settings(
    body: EscalationSettingsUpdate, tenant: Entitled, escalations: Escalations, session: Session
) -> EscalationSettingsRead:
    view = await escalations.save_settings(
        tenant,
        EscalationSettings(
            contact_phone=body.contact_phone,
            sms_enabled=body.sms_enabled,
            clarification_threshold=body.clarification_threshold,
            urgent_keywords=tuple(body.urgent_keywords),
        ),
    )
    await session.commit()
    return _settings_read(view)


@router.get("/escalations", response_model=EscalationListRead)
async def list_escalations(
    tenant: Entitled,
    escalations: Escalations,
    status: StatusFilter = "open",
    interaction_id: Annotated[UUID | None, Query()] = None,
) -> EscalationListRead:
    records = await escalations.list(tenant, status, interaction_id)
    return EscalationListRead(items=[_read(record) for record in records])


@router.patch("/escalations/{escalation_id}", response_model=EscalationRead)
async def resolve_escalation(
    escalation_id: UUID,
    body: EscalationResolve,
    tenant: Entitled,
    escalations: Escalations,
    session: Session,
) -> EscalationRead:
    record = await escalations.resolve(tenant, escalation_id)
    await session.commit()
    return _read(record)
