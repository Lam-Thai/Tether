"""`escalations` (a reason a person must look at an interaction, plus the state of
the owner's SMS alert) and `escalation_settings` (per-business rules, at most one
row, like `booking_rules`). Slice 13.

An escalation is recorded ONCE per `(interaction_id, reason)` (`uq_escalations_
interaction_reason`), whatever number of turns re-detect it. `sms_status` tracks the
alert: `pending` (recorded, send not attempted: a crash leaves this), `disabled`,
`logged` (demo mode), `skipped` (another escalation of the same interaction already
alerted: one SMS per interaction) and then Twilio's statuses; `failed` also covers our
own send failures, with a fixed `sms_error` code. `sms_sid` is unique for the webhook
lookup.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base

REASON_CODES = (
    "urgent",
    "human_requested",
    "complaint",
    "change_request",
    "booking_unresolved",
    "integration_failing",
    "clarification_exhausted",
)
SMS_STATUSES = (
    "pending",
    "disabled",
    "logged",
    "skipped",
    "accepted",
    "queued",
    "sending",
    "sent",
    "delivered",
    "undelivered",
    "failed",
)
_REASON_LIST = ", ".join(f"'{value}'" for value in REASON_CODES)
_SMS_LIST = ", ".join(f"'{value}'" for value in SMS_STATUSES)


class Escalation(Base):
    __tablename__ = "escalations"
    __table_args__ = (
        UniqueConstraint("interaction_id", "reason", name="uq_escalations_interaction_reason"),
        UniqueConstraint("sms_sid", name="uq_escalations_sms_sid"),
        CheckConstraint(f"reason IN ({_REASON_LIST})", name="ck_escalations_reason"),
        CheckConstraint("urgency IN ('normal', 'high')", name="ck_escalations_urgency"),
        CheckConstraint("status IN ('open', 'resolved')", name="ck_escalations_status"),
        CheckConstraint(f"sms_status IN ({_SMS_LIST})", name="ck_escalations_sms_status"),
        Index("ix_escalations_business_status_created", "business_id", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    interaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interactions.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    urgency: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'open'"), default="open"
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sms_status: Mapped[str] = mapped_column(Text, nullable=False)
    # A fixed code (`limit_reached`, ...) or Twilio's numeric ErrorCode; never provider text.
    sms_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sms_sid: Mapped[str | None] = mapped_column(Text, nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class EscalationSettingsRow(Base):
    __tablename__ = "escalation_settings"
    __table_args__ = (
        CheckConstraint(
            "contact_phone IS NULL OR contact_phone ~ '^\\+[1-9][0-9]{6,14}$'",
            name="ck_escalation_settings_contact_phone",
        ),
        CheckConstraint(
            "clarification_threshold BETWEEN 1 AND 5",
            name="ck_escalation_settings_threshold",
        ),
        CheckConstraint(
            "cardinality(urgent_keywords) <= 20", name="ck_escalation_settings_keywords"
        ),
    )

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True
    )
    contact_phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    sms_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    clarification_threshold: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("2"), default=2
    )
    urgent_keywords: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'"), default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
