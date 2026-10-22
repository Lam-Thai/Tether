"""`business_hours`: the weekly opening hours, one row per weekday (PRD §10.1).

`day_of_week` follows Python's `date.weekday()`: 0 = Monday ... 6 = Sunday.
Times are local wall-clock times in the business's `timezone`; turning them
into instants (DST included) is `services/directory.BusinessDirectory`'s
job, never the caller's. One window per day that ends the same day: a
closed day has no times, an open day has `opens_at < closes_at`.

A business with no rows has not set its hours, which is not the same as
being closed. `PUT /business/hours` replaces all seven rows in one
transaction, so a week is never half-written.
"""

import uuid
from datetime import datetime, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    SmallInteger,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class BusinessHours(Base):
    __tablename__ = "business_hours"
    __table_args__ = (
        UniqueConstraint("business_id", "day_of_week", name="uq_business_hours_business_day"),
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="ck_business_hours_day_of_week"),
        CheckConstraint(
            "(is_closed AND opens_at IS NULL AND closes_at IS NULL) OR "
            "(NOT is_closed AND opens_at IS NOT NULL AND closes_at IS NOT NULL "
            "AND opens_at < closes_at)",
            name="ck_business_hours_window",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    opens_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    closes_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
