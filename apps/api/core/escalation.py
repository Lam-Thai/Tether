"""Dependencies for escalations: the SQL store and the `EscalationService` builder.

`SqlEscalationStore` lives here, not in `repositories/`, because it speaks the service's
dataclasses (`services/escalation`); the SQL itself is in `repositories/escalations.py`.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings as app_settings
from core.db import get_session
from core.twilio import get_notifier
from models.escalation import Escalation, EscalationSettingsRow
from repositories import escalations as repo
from services.escalation import (
    EscalationRecord,
    EscalationService,
    EscalationSettings,
)
from services.notifier import Notifier


def inbox_url(interaction_id: uuid.UUID) -> str:
    return f"{app_settings.app_base_url.rstrip('/')}/dashboard/inbox/{interaction_id}"


def escalation_record(row: Escalation) -> EscalationRecord:
    return EscalationRecord(
        id=row.id,
        business_id=row.business_id,
        interaction_id=row.interaction_id,
        reason=row.reason,
        urgency=row.urgency,
        status=row.status,
        sms_status=row.sms_status,
        sms_error=row.sms_error,
        sms_sid=row.sms_sid,
        created_at=row.created_at,
        resolved_at=row.resolved_at,
        notified_at=row.notified_at,
    )


def _settings(row: EscalationSettingsRow) -> EscalationSettings:
    return EscalationSettings(
        contact_phone=row.contact_phone,
        sms_enabled=row.sms_enabled,
        clarification_threshold=row.clarification_threshold,
        urgent_keywords=tuple(row.urgent_keywords or ()),
    )


class SqlEscalationStore:
    """Implements `services.escalation.EscalationStore` over one session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_settings(self, business_id: uuid.UUID) -> EscalationSettings | None:
        row = await repo.get_settings(self._session, business_id)
        return None if row is None else _settings(row)

    async def upsert_settings(
        self, business_id: uuid.UUID, settings: EscalationSettings
    ) -> EscalationSettings:
        return _settings(await repo.upsert_settings(self._session, business_id, settings))

    async def insert_or_get(
        self,
        business_id: uuid.UUID,
        interaction_id: uuid.UUID,
        reason: str,
        urgency: str,
        sms_status: str,
        reopen: bool = False,
    ) -> tuple[EscalationRecord, str]:
        row, outcome = await repo.insert_or_get(
            self._session, business_id, interaction_id, reason, urgency, sms_status, reopen
        )
        return escalation_record(row), outcome

    async def get(
        self, business_id: uuid.UUID, escalation_id: uuid.UUID
    ) -> EscalationRecord | None:
        row = await repo.get_escalation(self._session, business_id, escalation_id)
        return None if row is None else escalation_record(row)

    async def list(
        self,
        business_id: uuid.UUID,
        *,
        status: str | None,
        interaction_id: uuid.UUID | None,
        limit: int,
    ) -> Sequence[EscalationRecord]:
        rows = await repo.list_escalations(
            self._session, business_id, status=status, interaction_id=interaction_id, limit=limit
        )
        return [escalation_record(row) for row in rows]

    async def resolve(
        self, business_id: uuid.UUID, escalation_id: uuid.UUID
    ) -> EscalationRecord | None:
        row = await repo.resolve_escalation(self._session, business_id, escalation_id)
        return None if row is None else escalation_record(row)

    async def claim_pending(self, business_id: uuid.UUID, escalation_id: uuid.UUID) -> bool:
        return await repo.claim_pending(self._session, business_id, escalation_id)

    async def finish_send(
        self,
        business_id: uuid.UUID,
        escalation_id: uuid.UUID,
        *,
        sms_status: str,
        sms_sid: str | None,
        sms_error: str | None,
        notified_at: datetime | None,
    ) -> EscalationRecord | None:
        row = await repo.finish_send(
            self._session,
            business_id,
            escalation_id,
            sms_status=sms_status,
            sms_sid=sms_sid,
            sms_error=sms_error,
            notified_at=notified_at,
        )
        return None if row is None else escalation_record(row)

    async def get_by_sid(self, sms_sid: str) -> EscalationRecord | None:
        row = await repo.get_by_sid(self._session, sms_sid)
        return None if row is None else escalation_record(row)

    async def advance_sms_status(
        self, sms_sid: str, *, seen_status: str, sms_status: str, sms_error: str | None
    ) -> bool:
        return await repo.advance_sms_status(
            self._session,
            sms_sid,
            seen_status=seen_status,
            sms_status=sms_status,
            sms_error=sms_error,
        )

    async def checkpoint(self) -> None:
        await self._session.commit()


def build_escalation_service(session: AsyncSession, notifier: Notifier) -> EscalationService:
    return EscalationService(SqlEscalationStore(session), notifier, inbox_url=inbox_url)


def get_escalation_service(
    session: AsyncSession = Depends(get_session),
    notifier: Notifier = Depends(get_notifier),
) -> EscalationService:
    return build_escalation_service(session, notifier)
