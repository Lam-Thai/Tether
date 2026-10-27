"""Repository for `interactions` and `interaction_messages`.

Every query is scoped by `business_id` from `TenantContext` (messages carry
a denormalized `business_id` for exactly this). Reads take no lock. Writes
that allocate `sequence_number` or change state go through
`lock_interaction`, which takes `SELECT ... FOR UPDATE` on the interaction
row with a `lock_timeout`; the service takes it only at persist time, never
across a model call. The unique `(interaction_id, sequence_number)` is the
backstop if that discipline ever slips.
"""

import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from sqlalchemy import exists, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import AppError
from models.booking import Booking
from models.escalation import Escalation
from models.interaction import Interaction, InteractionMessage
from models.operation import InteractionOperation
from repositories.base import business_scoped_select

# Shorter than the connection's 5s statement_timeout so a waiter gets a clean
# 409 instead of a cancelled statement while another request holds the row.
LOCK_TIMEOUT = "3s"
_LOCK_NOT_AVAILABLE = "55P03"
_QUERY_CANCELED = "57014"
# Transaction-level advisory lock that serializes new-interaction admission
# (count active, then insert) across all businesses. Arbitrary but fixed.
_ADMISSION_LOCK_KEY = 7_301_105_001


class InteractionBusyError(AppError):
    status_code = 409
    code = "interaction_busy"


async def create_interaction(
    session: AsyncSession, business_id: uuid.UUID, channel: str
) -> Interaction:
    interaction = Interaction(
        id=uuid.uuid4(), business_id=business_id, channel=channel, status="in_progress", state={}
    )
    session.add(interaction)
    await session.flush()
    return interaction


async def count_recent_active(
    session: AsyncSession, business_id: uuid.UUID, idle_minutes: int
) -> tuple[int, int]:
    """(all businesses, this business): active interactions touched in the last
    `idle_minutes`. Abandoned chats past that window stop counting."""
    cutoff = func.now() - func.make_interval(0, 0, 0, 0, 0, idle_minutes)
    result = await session.execute(
        select(
            func.count(),
            func.count().filter(Interaction.business_id == business_id),
        ).where(Interaction.status == "in_progress", Interaction.updated_at > cutoff)
    )
    everyone, mine = result.one()
    return int(everyone), int(mine)


async def lock_admission_and_count(
    session: AsyncSession, business_id: uuid.UUID, idle_minutes: int
) -> tuple[int, int]:
    """`count_recent_active` under the admission lock, held until the transaction
    ends. Create the new interaction in the same transaction so the count and
    the insert can't interleave with another admission."""
    await session.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
    try:
        await session.execute(select(func.pg_advisory_xact_lock(_ADMISSION_LOCK_KEY)))
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) in (_LOCK_NOT_AVAILABLE, _QUERY_CANCELED):
            raise InteractionBusyError(
                "Too many conversations are starting at once. Try again shortly."
            ) from exc
        raise
    return await count_recent_active(session, business_id, idle_minutes)


async def find_voice_call(session: AsyncSession, call_sid: str) -> Interaction | None:
    """The voice interaction for a Twilio CallSid. Not tenant-scoped on purpose: the call
    webhooks have no tenant, and the stored row (whose business came from the number
    called) decides it. Re-read from the database even if this session holds the row."""
    result = await session.execute(
        select(Interaction)
        .where(Interaction.channel == "voice", Interaction.external_call_id == call_sid)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def reload_interaction(
    session: AsyncSession, business_id: uuid.UUID, interaction_id: uuid.UUID
) -> None:
    """Re-reads the row into this session's object in place (`populate_existing`), so a
    later read sees what another worker committed. Never expires anything else."""
    await session.execute(
        business_scoped_select(Interaction, business_id)
        .where(Interaction.id == interaction_id)
        .execution_options(populate_existing=True)
    )


async def create_voice_call(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    phone_number_id: uuid.UUID,
    call_sid: str,
    caller: str | None,
) -> tuple[Interaction, bool]:
    """(the call's interaction, whether this inserted it). A CallSid that already has one
    (a racing duplicate webhook) returns that one: the voice-only unique index decides."""
    result = await session.execute(
        insert(Interaction)
        .values(
            id=uuid.uuid4(),
            business_id=business_id,
            channel="voice",
            phone_number_id=phone_number_id,
            external_call_id=call_sid,
            caller_identifier=caller,
            status="in_progress",
            state={},
        )
        .on_conflict_do_nothing(
            index_elements=["external_call_id"], index_where=text("channel = 'voice'")
        )
        .returning(Interaction.id)
    )
    created = result.scalar_one_or_none() is not None
    interaction = await find_voice_call(session, call_sid)
    assert interaction is not None
    return interaction, created


async def get_interaction(
    session: AsyncSession, business_id: uuid.UUID, interaction_id: uuid.UUID, channel: str
) -> Interaction | None:
    result = await session.execute(
        business_scoped_select(Interaction, business_id).where(
            Interaction.id == interaction_id, Interaction.channel == channel
        )
    )
    return result.scalar_one_or_none()


async def lock_interaction(
    session: AsyncSession, business_id: uuid.UUID, interaction_id: uuid.UUID, channel: str
) -> Interaction | None:
    """Re-reads the row under `FOR UPDATE`; held until the transaction ends."""
    await session.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
    try:
        result = await session.execute(
            business_scoped_select(Interaction, business_id)
            .where(Interaction.id == interaction_id, Interaction.channel == channel)
            .with_for_update()
            # Re-read under the lock even if this session already holds the object.
            .execution_options(populate_existing=True)
        )
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) in (_LOCK_NOT_AVAILABLE, _QUERY_CANCELED):
            raise InteractionBusyError(
                "This conversation is busy with another request. Try again shortly."
            ) from exc
        raise
    return result.scalar_one_or_none()


async def append_message(
    session: AsyncSession, interaction: Interaction, speaker: str, message_text: str
) -> InteractionMessage:
    """Adds the next `sequence_number`. The caller must hold `lock_interaction`."""
    current = await session.execute(
        select(func.coalesce(func.max(InteractionMessage.sequence_number), 0)).where(
            InteractionMessage.business_id == interaction.business_id,
            InteractionMessage.interaction_id == interaction.id,
        )
    )
    message = InteractionMessage(
        id=uuid.uuid4(),
        interaction_id=interaction.id,
        business_id=interaction.business_id,
        speaker=speaker,
        message_text=message_text,
        sequence_number=current.scalar_one() + 1,
    )
    session.add(message)
    await session.flush()
    return message


async def list_messages(
    session: AsyncSession,
    business_id: uuid.UUID,
    interaction_id: uuid.UUID,
    limit: int | None = None,
) -> Sequence[InteractionMessage]:
    """Oldest first. With `limit`, the most recent `limit` messages."""
    query = business_scoped_select(InteractionMessage, business_id).where(
        InteractionMessage.interaction_id == interaction_id
    )
    if limit is None:
        result = await session.execute(query.order_by(InteractionMessage.sequence_number))
        return result.scalars().all()
    result = await session.execute(
        query.order_by(InteractionMessage.sequence_number.desc()).limit(limit)
    )
    return list(reversed(result.scalars().all()))


async def save_interaction(session: AsyncSession, interaction: Interaction) -> Interaction:
    """Flushes edits, then reloads the server-computed `updated_at`."""
    await session.flush()
    await session.refresh(interaction)
    return interaction


async def closing_signals(
    session: AsyncSession, business_id: uuid.UUID, interaction_id: uuid.UUID
) -> tuple[bool, bool]:
    """(has a confirmed booking, a `create_booking` call was logged `booking_failed`):
    the two facts about a conversation only the backend's own records can state."""
    booked = await session.scalar(
        select(
            exists().where(
                Booking.business_id == business_id,
                Booking.interaction_id == interaction_id,
                Booking.status == "confirmed",
            )
        )
    )
    failed = await session.scalar(
        select(
            exists().where(
                InteractionOperation.business_id == business_id,
                InteractionOperation.interaction_id == interaction_id,
                InteractionOperation.tool_name == "create_booking",
                InteractionOperation.result["result"].astext == "booking_failed",
            )
        )
    )
    return bool(booked), bool(failed)


async def has_escalation(
    session: AsyncSession, business_id: uuid.UUID, interaction_id: uuid.UUID
) -> bool:
    """Any escalation (open or resolved) was recorded for this interaction."""
    return bool(
        await session.scalar(
            select(
                exists().where(
                    Escalation.business_id == business_id,
                    Escalation.interaction_id == interaction_id,
                )
            )
        )
    )


async def lock_idle(
    session: AsyncSession, business_id: uuid.UUID, idle_minutes: int, limit: int
) -> Sequence[Interaction]:
    """In-progress interactions of this business not touched for `idle_minutes`, locked
    `FOR UPDATE SKIP LOCKED` (a row a live request holds is skipped), oldest first."""
    cutoff = func.now() - func.make_interval(0, 0, 0, 0, 0, idle_minutes)
    result = await session.execute(
        business_scoped_select(Interaction, business_id)
        .where(Interaction.status == "in_progress", Interaction.updated_at < cutoff)
        .order_by(Interaction.updated_at, Interaction.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    return result.scalars().all()


class SqlConversationStore:
    """Binds the functions above to one session (`services.conversation.ConversationStore`)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_interaction(self, business_id: uuid.UUID, channel: str) -> Interaction:
        return await create_interaction(self._session, business_id, channel)

    async def lock_admission_and_count(
        self, business_id: uuid.UUID, idle_minutes: int
    ) -> tuple[int, int]:
        return await lock_admission_and_count(self._session, business_id, idle_minutes)

    async def get_interaction(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID, channel: str
    ) -> Interaction | None:
        return await get_interaction(self._session, business_id, interaction_id, channel)

    async def lock_interaction(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID, channel: str
    ) -> Interaction | None:
        return await lock_interaction(self._session, business_id, interaction_id, channel)

    async def append_message(
        self, interaction: Interaction, speaker: str, message_text: str
    ) -> InteractionMessage:
        return await append_message(self._session, interaction, speaker, message_text)

    async def list_messages(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID, limit: int | None = None
    ) -> Sequence[InteractionMessage]:
        return await list_messages(self._session, business_id, interaction_id, limit)

    async def save_interaction(self, interaction: Interaction) -> Interaction:
        return await save_interaction(self._session, interaction)

    async def closing_signals(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID
    ) -> tuple[bool, bool]:
        return await closing_signals(self._session, business_id, interaction_id)

    async def has_escalation(self, business_id: uuid.UUID, interaction_id: uuid.UUID) -> bool:
        return await has_escalation(self._session, business_id, interaction_id)

    async def lock_idle(
        self, business_id: uuid.UUID, idle_minutes: int, limit: int
    ) -> Sequence[Interaction]:
        return await lock_idle(self._session, business_id, idle_minutes, limit)

    async def checkpoint(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    @asynccontextmanager
    async def savepoint(self) -> AsyncIterator[None]:
        async with self._session.begin_nested():
            yield
