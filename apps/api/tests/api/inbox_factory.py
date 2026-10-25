"""Inserts synthetic interactions straight into Postgres for the inbox tests."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from core.db import async_session_factory
from models.interaction import Interaction, InteractionMessage
from models.operation import InteractionOperation

BASE = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)


async def add_interaction(
    business_id: uuid.UUID,
    *,
    started_at: datetime = BASE,
    status: str = "completed",
    outcome: str | None = "answered",
    intent: str | None = "FAQ",
    summary: str | None = None,
    summary_source: str | None = None,
    state: dict[str, Any] | None = None,
    caller: str | None = None,
    messages: tuple[tuple[str, str], ...] = (("agent", "Hi!"), ("customer", "Hello")),
    operations: tuple[tuple[str, str, dict[str, Any]], ...] = (),
    updated_at: datetime | None = None,
    interaction_id: uuid.UUID | None = None,
    channel: str = "test_chat",
) -> uuid.UUID:
    interaction_id = interaction_id or uuid.uuid4()
    closed = status != "in_progress"
    async with async_session_factory() as session:
        session.add(
            Interaction(
                id=interaction_id,
                business_id=business_id,
                channel=channel,
                caller_identifier=caller,
                intent=intent,
                status=status,
                outcome=outcome,
                summary=summary,
                summary_source=summary_source,
                state=state or {},
                started_at=started_at,
                ended_at=started_at + timedelta(minutes=5) if closed else None,
                created_at=started_at,
                updated_at=updated_at or started_at,
            )
        )
        # The interaction row must exist before its children.
        await session.flush()
        session.add_all(
            InteractionMessage(
                id=uuid.uuid4(),
                interaction_id=interaction_id,
                business_id=business_id,
                speaker=speaker,
                message_text=text,
                sequence_number=number,
                created_at=started_at + timedelta(seconds=number),
            )
            for number, (speaker, text) in enumerate(messages, start=1)
        )
        session.add_all(
            InteractionOperation(
                id=uuid.uuid4(),
                business_id=business_id,
                interaction_id=interaction_id,
                tool_name=tool,
                arguments={},
                status=op_status,
                result=result,
                duration_ms=12,
                created_at=started_at + timedelta(seconds=30 + position),
            )
            for position, (tool, op_status, result) in enumerate(operations)
        )
        await session.commit()
    return interaction_id


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


async def add_booking(
    business_id: uuid.UUID, interaction_id: uuid.UUID, status: str = "confirmed", day: int = 30
) -> None:
    from models.booking import Booking

    start = BASE + timedelta(days=day)
    async with async_session_factory() as session:
        session.add(
            Booking(
                id=uuid.uuid4(),
                business_id=business_id,
                interaction_id=interaction_id,
                service_name="Haircut",
                timezone="America/New_York",
                customer_name="Alex",
                start_time=start,
                end_time=start + timedelta(minutes=45),
                blocked_until=start + timedelta(minutes=45),
                status=status,
                calendar_event_id="ev" if status == "confirmed" else None,
                idempotency_key=str(uuid.uuid4()),
            )
        )
        await session.commit()
