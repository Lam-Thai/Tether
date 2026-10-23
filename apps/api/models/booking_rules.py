"""`booking_rules`: per-business slot rules (slice 10), at most one row per business.

No row means the owner never customized them; the defaults then come from
`Settings` (`core/booking_rules.default_booking_rules`). The CHECK bounds match
the `BOOKING_*` settings bounds.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class BookingRulesRow(Base):
    __tablename__ = "booking_rules"
    __table_args__ = (
        UniqueConstraint("business_id", name="uq_booking_rules_business_id"),
        CheckConstraint(
            "slot_interval_minutes BETWEEN 5 AND 240 AND slot_interval_minutes % 5 = 0",
            name="ck_booking_rules_slot_interval",
        ),
        CheckConstraint(
            "min_notice_minutes BETWEEN 0 AND 20160", name="ck_booking_rules_min_notice"
        ),
        CheckConstraint("max_days_ahead BETWEEN 1 AND 365", name="ck_booking_rules_max_days"),
        CheckConstraint("buffer_minutes BETWEEN 0 AND 240", name="ck_booking_rules_buffer"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    slot_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    min_notice_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    max_days_ahead: Mapped[int] = mapped_column(Integer, nullable=False)
    buffer_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
