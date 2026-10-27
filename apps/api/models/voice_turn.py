"""`voice_turns`: who is working on a call's turn right now, so a slow or lost worker can
be waited for or taken over without running the turn twice.

One row per (interaction, turn), UNIQUE: a retried `/input` webhook finds the row instead
of starting a second worker. `claim` names the worker that owns the turn and
`heartbeat_at` is refreshed while it runs; a stale heartbeat (a serverless function
frozen after it answered with a holding phrase) lets the wait webhook take the turn over
with a conditional UPDATE on the claim it saw. Whether the turn was ANSWERED is decided
by the conversation engine's turn key, written with the messages in one transaction;
`status` here only mirrors it. `utterance` (redacted, as stored in the transcript) is
kept only until then, since a takeover needs it and the webhook that redirects to
`/wait` doesn't carry it.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class VoiceTurn(Base):
    __tablename__ = "voice_turns"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'answered', 'failed')", name="ck_voice_turns_status"
        ),
        CheckConstraint("turn >= 1", name="ck_voice_turns_turn"),
        UniqueConstraint("interaction_id", "turn", name="uq_voice_turns_interaction_turn"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    interaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interactions.id", ondelete="CASCADE"), nullable=False
    )
    turn: Mapped[int] = mapped_column(Integer, nullable=False)
    utterance: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    claim: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=_utcnow,
        onupdate=func.now(),
        nullable=False,
    )
