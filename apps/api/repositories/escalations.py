"""Repository for `escalations` and `escalation_settings`.

Every query is scoped by `business_id` (from `TenantContext`) through
`repositories/base.py`, with ONE exception: `get_by_sid` / `advance_sms_status` look an
escalation up by its Twilio MessageSid. They serve the provider's webhook, which has no
tenant (the request is authenticated by Twilio's signature instead). The sid is an
unguessable provider id, unique across all rows (`uq_escalations_sms_sid`), and the
lookup returns only the status fields it updates: the same shape as the Stripe event
dedupe in `repositories/billing.py`.

Rows are flushed, not committed: the service decides when to commit (it commits before
every provider call).
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.escalation import Escalation, EscalationSettingsRow
from repositories.base import business_scoped_select


class SettingsValues(Protocol):
    @property
    def contact_phone(self) -> str | None: ...
    @property
    def sms_enabled(self) -> bool: ...
    @property
    def clarification_threshold(self) -> int: ...
    @property
    def urgent_keywords(self) -> Sequence[str]: ...


async def get_settings(
    session: AsyncSession, business_id: uuid.UUID
) -> EscalationSettingsRow | None:
    result = await session.execute(business_scoped_select(EscalationSettingsRow, business_id))
    return result.scalar_one_or_none()


async def upsert_settings(
    session: AsyncSession, business_id: uuid.UUID, values: SettingsValues
) -> EscalationSettingsRow:
    """INSERT ... ON CONFLICT (business_id) DO UPDATE, in the caller's transaction."""
    columns = {
        "contact_phone": values.contact_phone,
        "sms_enabled": values.sms_enabled,
        "clarification_threshold": values.clarification_threshold,
        "urgent_keywords": list(values.urgent_keywords),
    }
    stmt = (
        insert(EscalationSettingsRow)
        .values(business_id=business_id, **columns)
        .on_conflict_do_update(
            index_elements=[EscalationSettingsRow.business_id],
            set_={**columns, "updated_at": func.clock_timestamp()},
        )
        .returning(EscalationSettingsRow)
    )
    result = await session.execute(stmt, execution_options={"populate_existing": True})
    return result.scalar_one()


# An alert that is out or on its way: a later escalation of the same interaction needs no
# second SMS. `disabled`, `failed`, `undelivered` and `skipped` never reached the owner.
ALERT_OUT_STATUSES = ("pending", "sending", "logged", "accepted", "queued", "sent", "delivered")


async def insert_or_get(
    session: AsyncSession,
    business_id: uuid.UUID,
    interaction_id: uuid.UUID,
    reason: str,
    urgency: str,
    sms_status: str,
    reopen: bool = False,
) -> tuple[Escalation, str]:
    """Records `(interaction_id, reason)` once. Returns `(row, outcome)` where outcome is
    `created`, `reopened` (a RESOLVED row reopened because `reopen` is set) or `existing`.

    One SMS per interaction: a row that would be `pending` is stored `skipped` instead
    when an EARLIER escalation of the interaction has an alert out or on its way
    (`ALERT_OUT_STATUSES`); a high-urgency one only counts earlier HIGH-urgency alerts,
    so an urgent matter is never muted by a routine alert. The check and the write run
    under a per-interaction advisory lock (released by the commit), so two reasons racing
    for one interaction cannot both be the first.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"escalation:{interaction_id}"},
    )
    earlier = select(Escalation.id).where(
        Escalation.business_id == business_id,
        Escalation.interaction_id == interaction_id,
        Escalation.reason != reason,
        Escalation.sms_status.in_(ALERT_OUT_STATUSES),
    )
    if urgency == "high":
        earlier = earlier.where(Escalation.urgency == "high")
    alerted = await session.scalar(earlier.limit(1)) is not None
    initial = "skipped" if alerted and sms_status == "pending" else sms_status

    stmt = (
        insert(Escalation)
        .values(
            id=uuid.uuid4(),
            business_id=business_id,
            interaction_id=interaction_id,
            reason=reason,
            urgency=urgency,
            sms_status=initial,
        )
        .on_conflict_do_nothing(constraint="uq_escalations_interaction_reason")
        .returning(Escalation)
    )
    created = (
        await session.execute(stmt, execution_options={"populate_existing": True})
    ).scalar_one_or_none()
    if created is not None:
        return created, "created"
    if reopen:
        reopened = await session.execute(
            update(Escalation)
            .where(
                Escalation.business_id == business_id,
                Escalation.interaction_id == interaction_id,
                Escalation.reason == reason,
                Escalation.status == "resolved",
            )
            .values(
                status="open",
                resolved_at=None,
                urgency=urgency,
                sms_status=initial,
                # A fresh alert cycle: the old message's ids no longer describe this row.
                sms_sid=None,
                sms_error=None,
                notified_at=None,
                updated_at=func.clock_timestamp(),
            )
            .returning(Escalation)
            .execution_options(populate_existing=True)
        )
        row = reopened.scalar_one_or_none()
        if row is not None:
            return row, "reopened"
    existing = await session.execute(
        business_scoped_select(Escalation, business_id)
        .where(Escalation.interaction_id == interaction_id, Escalation.reason == reason)
        .execution_options(populate_existing=True)
    )
    return existing.scalar_one(), "existing"


async def get_escalation(
    session: AsyncSession, business_id: uuid.UUID, escalation_id: uuid.UUID
) -> Escalation | None:
    result = await session.execute(
        business_scoped_select(Escalation, business_id)
        .where(Escalation.id == escalation_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def list_escalations(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    status: str | None,
    interaction_id: uuid.UUID | None,
    limit: int,
) -> Sequence[Escalation]:
    """Newest first. Served by `ix_escalations_business_status_created`."""
    query = business_scoped_select(Escalation, business_id)
    if status is not None:
        query = query.where(Escalation.status == status)
    if interaction_id is not None:
        query = query.where(Escalation.interaction_id == interaction_id)
    result = await session.execute(
        query.order_by(Escalation.created_at.desc(), Escalation.id.desc())
        .limit(limit)
        .execution_options(populate_existing=True)
    )
    return result.scalars().all()


async def list_for_interactions(
    session: AsyncSession, business_id: uuid.UUID, interaction_ids: Sequence[uuid.UUID]
) -> Sequence[Escalation]:
    """Every escalation of these interactions in ONE query (the inbox page, no N+1).
    Served by the leading column of `uq_escalations_interaction_reason`."""
    if not interaction_ids:
        return []
    result = await session.execute(
        business_scoped_select(Escalation, business_id)
        .where(Escalation.interaction_id.in_(interaction_ids))
        .order_by(Escalation.created_at.desc(), Escalation.id.desc())
        .execution_options(populate_existing=True)
    )
    return result.scalars().all()


async def resolve_escalation(
    session: AsyncSession, business_id: uuid.UUID, escalation_id: uuid.UUID
) -> Escalation | None:
    """Open -> resolved; an already resolved row is returned unchanged."""
    result = await session.execute(
        update(Escalation)
        .where(
            Escalation.business_id == business_id,
            Escalation.id == escalation_id,
            Escalation.status == "open",
        )
        .values(
            status="resolved",
            resolved_at=func.clock_timestamp(),
            updated_at=func.clock_timestamp(),
        )
        .returning(Escalation)
        .execution_options(populate_existing=True)
    )
    changed = result.scalar_one_or_none()
    return (
        changed
        if changed is not None
        else await get_escalation(session, business_id, escalation_id)
    )


async def claim_pending(
    session: AsyncSession, business_id: uuid.UUID, escalation_id: uuid.UUID
) -> bool:
    """`pending -> sending`, conditional: only the caller that wins sends the alert."""
    result = await session.execute(
        update(Escalation)
        .where(
            Escalation.business_id == business_id,
            Escalation.id == escalation_id,
            Escalation.sms_status == "pending",
        )
        .values(sms_status="sending", updated_at=func.clock_timestamp())
        .returning(Escalation.id)
    )
    return result.scalar_one_or_none() is not None


async def finish_send(
    session: AsyncSession,
    business_id: uuid.UUID,
    escalation_id: uuid.UUID,
    *,
    sms_status: str,
    sms_sid: str | None,
    sms_error: str | None,
    notified_at: datetime | None,
) -> Escalation | None:
    """Stores the outcome of a claimed send. Only a row still `sending` is written, so a
    status callback that already moved it further is never overwritten."""
    result = await session.execute(
        update(Escalation)
        .where(
            Escalation.business_id == business_id,
            Escalation.id == escalation_id,
            Escalation.sms_status == "sending",
        )
        .values(
            sms_status=sms_status,
            sms_sid=sms_sid,
            sms_error=sms_error,
            notified_at=notified_at,
            updated_at=func.clock_timestamp(),
        )
        .returning(Escalation)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def get_by_sid(session: AsyncSession, sms_sid: str) -> Escalation | None:
    """UNSCOPED on purpose (see the module docstring): the Twilio status webhook."""
    result = await session.execute(
        select(Escalation)
        .where(Escalation.sms_sid == sms_sid)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def advance_sms_status(
    session: AsyncSession,
    sms_sid: str,
    *,
    seen_status: str,
    sms_status: str,
    sms_error: str | None,
) -> bool:
    """Conditional on the status the caller read (`WHERE sms_status = :seen`), so two
    callbacks racing cannot move the status backwards. UNSCOPED, like `get_by_sid`."""
    result = await session.execute(
        update(Escalation)
        .where(Escalation.sms_sid == sms_sid, Escalation.sms_status == seen_status)
        .values(
            sms_status=sms_status,
            sms_error=sms_error,
            updated_at=func.clock_timestamp(),
        )
        .returning(Escalation.id)
    )
    return result.scalar_one_or_none() is not None
