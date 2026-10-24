"""`MemoryBookingStore`: the `BookingStore` port in memory, with the same rules as the
schema (overlapping active rows of one business conflict; an active idempotency key is
unique). The real constraint is tested against Postgres in
`tests/repositories/test_bookings.py` and `tests/services/test_booking_concurrency.py`."""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from models.booking import ACTIVE_STATUSES, Booking, KeyConflict, NewBooking, SlotConflict


class MemoryBookingStore:
    def __init__(self) -> None:
        self.rows: list[Booking] = []
        self.checkpoints = 0
        # Raised by the next `resolve_pending` to `confirmed` (the save after Google).
        self.confirm_error: Exception | None = None
        self.stale_ids: set[uuid.UUID] = set()

    async def find_active_by_key(self, business_id, idempotency_key):
        return next(
            (
                r
                for r in self.rows
                if r.business_id == business_id
                and r.idempotency_key == idempotency_key
                and r.status in ACTIVE_STATUSES
            ),
            None,
        )

    async def reserve(self, business_id, new: NewBooking):
        active = [
            r for r in self.rows if r.business_id == business_id and r.status in ACTIVE_STATUSES
        ]
        if any(r.idempotency_key == new.idempotency_key for r in active):
            return KeyConflict()
        if any(
            r.start_time < new.blocked_until and new.start_time < r.blocked_until for r in active
        ):
            return SlotConflict()
        row = Booking(
            id=uuid.uuid4(),
            business_id=business_id,
            interaction_id=new.interaction_id,
            service_id=new.service_id,
            service_name=new.service_name,
            customer_name=new.customer_name,
            customer_phone=new.customer_phone,
            start_time=new.start_time,
            end_time=new.end_time,
            blocked_until=new.blocked_until,
            status="pending",
            idempotency_key=new.idempotency_key,
            calendar_event_id=None,
            failure_reason=None,
            created_at=datetime.now(UTC),
        )
        self.rows.append(row)
        return row

    async def resolve_pending(
        self, business_id, booking_id, *, status, calendar_event_id=None, failure_reason=None
    ):
        if status == "confirmed" and self.confirm_error is not None:
            raise self.confirm_error
        row = next(
            (
                r
                for r in self.rows
                if r.id == booking_id and r.business_id == business_id and r.status == "pending"
            ),
            None,
        )
        if row is None:
            return None
        row.status = status
        row.failure_reason = failure_reason
        if calendar_event_id is not None:
            row.calendar_event_id = calendar_event_id
        return row

    async def expire_stale_pending(self, business_id, older_than_seconds):
        expired = 0
        for row in self.rows:
            if (
                row.business_id == business_id
                and row.id in self.stale_ids
                and row.status == "pending"
            ):
                row.status = "needs_reconciliation"
                row.failure_reason = "pending_timeout"
                expired += 1
        return expired

    async def list_for_interaction(self, business_id, interaction_id):
        return [
            r
            for r in self.rows
            if r.business_id == business_id and r.interaction_id == interaction_id
        ]

    async def checkpoint(self):
        self.checkpoints += 1

    @asynccontextmanager
    async def savepoint(self):
        yield

    def of(self, business_id) -> list[Booking]:
        return [r for r in self.rows if r.business_id == business_id]
