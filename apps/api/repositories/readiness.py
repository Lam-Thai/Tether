"""Facts behind the activation checklist and the integration status (slice 16).

Every query is scoped by `business_id` (from `TenantContext`). Plain reads, no writes.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, and_, cast, exists, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from models.calendar import CalendarConnection
from models.escalation import Escalation, EscalationSettingsRow
from models.hours import BusinessHours
from models.interaction import Interaction, InteractionMessage
from models.phone_number import PhoneNumber
from models.service import Service

SMS_FAILED_STATUSES = ("failed", "undelivered")


async def count_active_services(session: AsyncSession, business_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(Service)
            .where(Service.business_id == business_id, Service.active)
        )
        or 0
    )


async def has_hours(session: AsyncSession, business_id: uuid.UUID) -> bool:
    return bool(
        await session.scalar(select(exists().where(BusinessHours.business_id == business_id)))
    )


async def has_escalation_contact(session: AsyncSession, business_id: uuid.UUID) -> bool:
    return bool(
        await session.scalar(
            select(
                exists().where(
                    EscalationSettingsRow.business_id == business_id,
                    EscalationSettingsRow.contact_phone.is_not(None),
                )
            )
        )
    )


async def phone_numbers(session: AsyncSession, business_id: uuid.UUID) -> Sequence[PhoneNumber]:
    """The business's numbers, oldest first."""
    result = await session.execute(
        select(PhoneNumber)
        .where(PhoneNumber.business_id == business_id)
        .order_by(PhoneNumber.created_at, PhoneNumber.id)
    )
    return result.scalars().all()


async def has_tested_chat(session: AsyncSession, business_id: uuid.UUID) -> bool:
    """A `test_chat` interaction where the agent replied to a customer message with a
    real reply: not the greeting alone, and not one of the engine's fallback replies
    (`state.fallback_messages` lists their sequence numbers)."""
    reply = aliased(InteractionMessage)
    asked = exists().where(
        InteractionMessage.interaction_id == reply.interaction_id,
        InteractionMessage.business_id == business_id,
        InteractionMessage.speaker == "customer",
        InteractionMessage.sequence_number < reply.sequence_number,
    )
    fallbacks = func.coalesce(Interaction.state["fallback_messages"], cast(text("'[]'"), JSONB))
    query: Select = select(
        exists().where(
            and_(
                Interaction.business_id == business_id,
                Interaction.channel == "test_chat",
                reply.interaction_id == Interaction.id,
                reply.business_id == business_id,
                reply.speaker == "agent",
                asked,
                ~fallbacks.contains(func.to_jsonb(reply.sequence_number)),
            )
        )
    )
    return bool(await session.scalar(query))


async def calendar_updated_at(session: AsyncSession, business_id: uuid.UUID) -> datetime | None:
    return await session.scalar(
        select(CalendarConnection.updated_at).where(CalendarConnection.business_id == business_id)
    )


async def failed_sms_alerts(
    session: AsyncSession, business_id: uuid.UUID, since: datetime
) -> Sequence[tuple[str | None, datetime]]:
    """`(sms_error, latest update)` for escalations whose SMS alert failed since `since`,
    grouped by error code."""
    result = await session.execute(
        select(Escalation.sms_error, func.max(Escalation.updated_at))
        .where(
            Escalation.business_id == business_id,
            Escalation.sms_status.in_(SMS_FAILED_STATUSES),
            Escalation.updated_at >= since,
        )
        .group_by(Escalation.sms_error)
    )
    return [(code, latest) for code, latest in result.all()]
