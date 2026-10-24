"""`bookings`: appointments the agent made (slice 11), one row per booking attempt.

Status lifecycle (`services/booking.py` owns it):

- `pending`: reserved in Postgres, the Google event not created yet. Committed BEFORE
  the provider call, so it blocks the slot for every other caller while it runs.
- `confirmed`: the Google event exists (`calendar_event_id`) and the row says so. The
  only status a customer is ever told about as a booking.
- `failed`: the event was not created (calendar down, revoked, nothing confirmed).
- `cancelled`: no longer holds the slot (owner or a later slice cancelled it).
- `needs_reconciliation`: the event MAY exist at Google but the row couldn't be
  confirmed (save failed and the compensating cancel failed too, or a `pending` row
  outlived `PENDING_TIMEOUT`). Someone has to look; it never blocks the slot here,
  since Google's own free/busy still shows the event if it exists.

Zero double bookings is enforced by Postgres, not by the service:
`ex_bookings_no_overlap` excludes two ACTIVE rows (`pending`/`confirmed`) of one business
whose `[start_time, blocked_until)` ranges overlap. `blocked_until` is the end plus the
business's buffer at booking time, so the buffer holds between two bookings as well.
One calendar is one chair (the slice 10 rule): any overlap is a conflict, whatever
the service. `idempotency_key` is unique among active rows of a business, so a retried
tool call finds the booking it already made instead of making a second one; a failed
attempt stays as history and the key can be used again.

`service_name` is a snapshot (the service may be renamed or deleted later).
`customer_name` / `customer_phone` are customer data: synthetic only in tests and seeds.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base

BOOKING_STATUSES = ("pending", "confirmed", "failed", "cancelled", "needs_reconciliation")
# The statuses that hold a slot (the exclusion constraint and the idempotency key).
ACTIVE_STATUSES = ("pending", "confirmed")
_STATUS_LIST = ", ".join(f"'{status}'" for status in BOOKING_STATUSES)
ACTIVE_PREDICATE = "status IN ('pending', 'confirmed')"

MAX_CUSTOMER_NAME_CHARS = 80
MAX_CUSTOMER_PHONE_CHARS = 32


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint(f"status IN ({_STATUS_LIST})", name="ck_bookings_status"),
        CheckConstraint("end_time > start_time", name="ck_bookings_time_order"),
        CheckConstraint("blocked_until >= end_time", name="ck_bookings_blocked_until"),
        CheckConstraint(
            f"char_length(customer_name) BETWEEN 1 AND {MAX_CUSTOMER_NAME_CHARS}",
            name="ck_bookings_customer_name",
        ),
        CheckConstraint(
            f"customer_phone IS NULL OR char_length(customer_phone) <= {MAX_CUSTOMER_PHONE_CHARS}",
            name="ck_bookings_customer_phone",
        ),
        CheckConstraint(
            "status <> 'confirmed' OR calendar_event_id IS NOT NULL",
            name="ck_bookings_confirmed_has_event",
        ),
        # Needs the btree_gist extension (created by the migration) for `business_id =`.
        ExcludeConstraint(
            ("business_id", "="),
            (func.tstzrange(text("start_time"), text("blocked_until")), "&&"),
            using="gist",
            where=text(ACTIVE_PREDICATE),
            name="ex_bookings_no_overlap",
        ),
        Index(
            "uq_bookings_active_idempotency_key",
            "business_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text(ACTIVE_PREDICATE),
        ),
        Index("ix_bookings_business_id_start_time", "business_id", "start_time"),
        Index("ix_bookings_interaction_id", "interaction_id"),
        Index("ix_bookings_service_id", "service_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False
    )
    # A booking outlives the conversation and the service that produced it.
    interaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interactions.id", ondelete="SET NULL"), nullable=True
    )
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("services.id", ondelete="SET NULL"), nullable=True
    )
    service_name: Mapped[str] = mapped_column(String, nullable=False)
    calendar_event_id: Mapped[str | None] = mapped_column(String, nullable=True)
    customer_name: Mapped[str] = mapped_column(String, nullable=False)
    customer_phone: Mapped[str | None] = mapped_column(String, nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    blocked_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    # A fixed reason code for `failed` / `needs_reconciliation`, never provider text.
    failure_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=_utcnow,
        onupdate=func.clock_timestamp(),
        nullable=False,
    )


# Plain values the booking store (`repositories/bookings.py`) and its port
# (`services/booking.BookingStore`) exchange.


@dataclass(frozen=True)
class NewBooking:
    interaction_id: uuid.UUID | None
    service_id: uuid.UUID
    service_name: str
    customer_name: str
    customer_phone: str | None
    start_time: datetime
    end_time: datetime
    blocked_until: datetime
    idempotency_key: str


@dataclass(frozen=True)
class SlotConflict:
    """Another active booking of this business overlaps the requested range."""


@dataclass(frozen=True)
class KeyConflict:
    """An active booking with this idempotency key already exists."""
