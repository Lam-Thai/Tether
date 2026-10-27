"""`interactions` (one conversation: a test chat now, a phone call later) and
its ordered `interaction_messages`.

`status` is `in_progress | completed | failed | abandoned`; `intent` is the
PRIMARY intent of the conversation; `outcome` and the summary are written when it
closes (`services/lifecycle.py`, `services/summarizer.py`).

`state` is the conversation's working memory (known entities, failures,
clarification attempts). All of it lives here, never in process memory.
`interaction_messages.business_id` is denormalized so every query scopes by
`business_id` directly. `(interaction_id, sequence_number)` is unique: the
repository allocates numbers under a row lock on the interaction, and this
constraint is the backstop.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base

STATUSES = ("in_progress", "completed", "failed", "abandoned")
OUTCOMES = ("booked", "answered", "escalated", "failed", "abandoned")
_STATUS_LIST = ", ".join(f"'{value}'" for value in STATUSES)
_OUTCOME_LIST = ", ".join(f"'{value}'" for value in OUTCOMES)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Interaction(Base):
    __tablename__ = "interactions"
    __table_args__ = (
        CheckConstraint("channel IN ('test_chat', 'voice')", name="ck_interactions_channel"),
        CheckConstraint(f"status IN ({_STATUS_LIST})", name="ck_interactions_status"),
        CheckConstraint(
            f"outcome IS NULL OR outcome IN ({_OUTCOME_LIST})", name="ck_interactions_outcome"
        ),
        CheckConstraint(
            "summary_source IS NULL OR summary_source IN ('ai', 'template')",
            name="ck_interactions_summary_source",
        ),
        Index("ix_interactions_business_id_created_at", "business_id", "created_at"),
        # Concurrency admission counts recently active interactions (`services/usage.py`).
        Index(
            "ix_interactions_active_updated_at",
            "updated_at",
            postgresql_where=text("status = 'in_progress'"),
        ),
        # Inbox: keyset pagination and the status / intent filters (`services/inbox.py`).
        Index("ix_interactions_business_started_id", "business_id", "started_at", "id"),
        Index("ix_interactions_business_id_status", "business_id", "status"),
        Index("ix_interactions_business_id_intent", "business_id", "intent"),
        # One interaction per Twilio CallSid, so a retried or duplicate webhook can never
        # start a second one (`services/call_session.py`). Voice only: other channels
        # (the eval harness's seeds) use the column as a per-business key of their own.
        Index(
            "uq_interactions_voice_external_call_id",
            "external_call_id",
            unique=True,
            postgresql_where=text("channel = 'voice'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String, nullable=False)
    # The number a call came in on; kept (NULL) when the number is later disconnected.
    phone_number_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("phone_numbers.id", ondelete="SET NULL"), nullable=True
    )
    # Voice: Twilio's CallSid.
    external_call_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Voice: the caller ID (E.164) Twilio reported; masked in inbox lists.
    caller_identifier: Mapped[str | None] = mapped_column(String, nullable=True)
    intent: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="in_progress", server_default="in_progress"
    )
    # How it ended, derived by the backend (`services/lifecycle.py`); NULL while in progress.
    outcome: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_source: Mapped[str | None] = mapped_column(String, nullable=True)
    # Lease taken by a summarize call so two concurrent ones don't both call the model.
    summary_claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    state: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=_utcnow, nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


class InteractionMessage(Base):
    __tablename__ = "interaction_messages"
    __table_args__ = (
        CheckConstraint("speaker IN ('agent', 'customer')", name="ck_interaction_messages_speaker"),
        UniqueConstraint(
            "interaction_id", "sequence_number", name="uq_interaction_messages_interaction_seq"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    interaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interactions.id", ondelete="CASCADE"), nullable=False
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    speaker: Mapped[str] = mapped_column(String, nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=_utcnow, nullable=False
    )
