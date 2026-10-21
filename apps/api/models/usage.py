"""`usage_events` (append-only audit log, PRD §10.1) and `usage_counters`
(one row per scope, kind and UTC day — what limits are enforced against).

Counters are the race-safe part: `repositories/usage.py` bumps them with a
conditional upsert, so concurrent requests at a limit can't overshoot it.
A counter with `business_id IS NULL` is the global (all-businesses) one; the
two partial unique indexes give each scope exactly one row per kind per day.
Events keep `business_id` nullable for calls metered globally only (Stripe
webhooks). Both cascade with the business, like every other tenant table.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base

USAGE_KINDS = (
    "gemini_request",
    "gemini_tokens",
    "tts_request",
    "call",
    "call_seconds",
    "sms",
    "calendar_call",
    "billing_call",
    "failed_request",
)
_KIND_LIST = ", ".join(f"'{kind}'" for kind in USAGE_KINDS)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (
        CheckConstraint(f"event_type IN ({_KIND_LIST})", name="ck_usage_events_event_type"),
        CheckConstraint("units >= 0", name="ck_usage_events_units"),
        Index("ix_usage_events_business_type_created", "business_id", "event_type", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=True
    )
    interaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("interactions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    estimated_cost: Mapped[Decimal] = mapped_column(
        Numeric(14, 8), nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=_utcnow, nullable=False
    )


class UsageCounter(Base):
    __tablename__ = "usage_counters"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_KIND_LIST})", name="ck_usage_counters_kind"),
        CheckConstraint("count >= 0", name="ck_usage_counters_count"),
        Index(
            "uq_usage_counters_global",
            "kind",
            "window_date",
            unique=True,
            postgresql_where=text("business_id IS NULL"),
        ),
        Index(
            "uq_usage_counters_business",
            "business_id",
            "kind",
            "window_date",
            unique=True,
            postgresql_where=text("business_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    # The UTC calendar day this counter covers (see `services/usage.py`).
    window_date: Mapped[date] = mapped_column(Date, nullable=False)
    count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
