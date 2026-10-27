"""Repository for `voice_turns` (see `models/voice_turn.py`).

Each function is one statement; `core/voice.SqlTurnClaims` runs every claim operation in
its own committed transaction (a session factory, like `core/usage.SqlUsageStore`), so a
claim, heartbeat or takeover is visible to every worker at once. Reads are scoped by
`business_id`; updates address a row by the id a scoped read returned. Staleness is
judged in database time, so app-server clocks never have to agree.
"""

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.voice_turn import VoiceTurn
from repositories.base import business_scoped_select


async def insert_claim(
    session: AsyncSession,
    *,
    business_id: uuid.UUID,
    interaction_id: uuid.UUID,
    turn: int,
    utterance: str,
    claim: uuid.UUID,
) -> bool:
    """Inserts the pending turn; False when (interaction, turn) already exists."""
    result = await session.execute(
        insert(VoiceTurn)
        .values(
            id=uuid.uuid4(),
            business_id=business_id,
            interaction_id=interaction_id,
            turn=turn,
            utterance=utterance,
            status="pending",
            claim=claim,
            heartbeat_at=func.now(),
        )
        .on_conflict_do_nothing(constraint="uq_voice_turns_interaction_turn")
        .returning(VoiceTurn.id)
    )
    return result.scalar_one_or_none() is not None


async def get_turn(
    session: AsyncSession, business_id: uuid.UUID, interaction_id: uuid.UUID, turn: int
) -> VoiceTurn | None:
    result = await session.execute(
        business_scoped_select(VoiceTurn, business_id)
        .where(VoiceTurn.interaction_id == interaction_id, VoiceTurn.turn == turn)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def max_turn(session: AsyncSession, business_id: uuid.UUID, interaction_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.coalesce(func.max(VoiceTurn.turn), 0)).where(
            VoiceTurn.business_id == business_id, VoiceTurn.interaction_id == interaction_id
        )
    )
    return int(result.scalar_one())


async def heartbeat(session: AsyncSession, turn_id: uuid.UUID, claim: uuid.UUID) -> bool:
    result = await session.execute(
        update(VoiceTurn)
        .where(VoiceTurn.id == turn_id, VoiceTurn.claim == claim, VoiceTurn.status == "pending")
        .values(heartbeat_at=func.now(), updated_at=func.now())
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def take_over(
    session: AsyncSession,
    turn_id: uuid.UUID,
    seen: uuid.UUID,
    new: uuid.UUID,
    stale_after_seconds: float,
) -> bool:
    """Moves a pending turn to `new` only if it still has the claim `seen` and its
    heartbeat is older than `stale_after_seconds` (two takers: one wins)."""
    stale_before = func.now() - func.make_interval(0, 0, 0, 0, 0, 0, stale_after_seconds)
    result = await session.execute(
        update(VoiceTurn)
        .where(
            VoiceTurn.id == turn_id,
            VoiceTurn.claim == seen,
            VoiceTurn.status == "pending",
            VoiceTurn.heartbeat_at < stale_before,
        )
        .values(claim=new, heartbeat_at=func.now(), updated_at=func.now())
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def finish(session: AsyncSession, turn_id: uuid.UUID, claim: uuid.UUID, status: str) -> None:
    """`answered` (by whoever stored it) or `failed` (only by the current owner); the
    utterance is dropped either way."""
    query = update(VoiceTurn).where(VoiceTurn.id == turn_id)
    if status != "answered":
        query = query.where(VoiceTurn.claim == claim, VoiceTurn.status == "pending")
    await session.execute(query.values(status=status, utterance=None, updated_at=func.now()))
