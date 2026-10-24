"""Repository for `bookings`. Every query is scoped by `business_id` from `TenantContext`.

The double-booking guarantees live in the schema (`models/booking.py`), so the two
ways an insert can lose a race come back as values, not exceptions:

- `SlotConflict`: `ex_bookings_no_overlap` rejected the row (another active booking
  of this business overlaps it, whoever inserted first).
- `KeyConflict`: an active booking with the same idempotency key exists (the same
  tool call is already running or already made it).

Concurrent inserts that race on an exclusion constraint can deadlock in Postgres (each
waits on the other's uncommitted row; one is aborted with a deadlock error, not a
constraint error). So `reserve` and `expire_stale_pending` first take a per-business
transaction-level advisory lock: reservations of one business are serialized for the
few milliseconds until the caller's `checkpoint()` commits, and the constraint stays
the backstop. The lock is never held across a provider call (the service commits
before calling Google).

Status changes are conditional on the row still being `pending` (`resolve_pending`),
so a booking already moved on (confirmed, or swept to `needs_reconciliation`) is
never overwritten; the caller gets `None` and decides. Writes are single statements
with `RETURNING` and `populate_existing`, so a returned row always reflects the
database. Nothing here commits except `checkpoint()`.
"""

import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from sqlalchemy import func, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.booking import ACTIVE_STATUSES, Booking, KeyConflict, NewBooking, SlotConflict
from repositories.base import business_scoped_select

OVERLAP_CONSTRAINT = "ex_bookings_no_overlap"
KEY_INDEX = "uq_bookings_active_idempotency_key"


async def lock_reservations(session: AsyncSession, business_id: uuid.UUID) -> None:
    """Serializes reservations per business until the transaction ends (re-entrant)."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('bookings:' || CAST(:b AS text), 0))"),
        {"b": str(business_id)},
    )


async def find_active_by_key(
    session: AsyncSession, business_id: uuid.UUID, idempotency_key: str
) -> Booking | None:
    result = await session.execute(
        business_scoped_select(Booking, business_id)
        .where(Booking.idempotency_key == idempotency_key)
        .where(Booking.status.in_(ACTIVE_STATUSES))
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def reserve(
    session: AsyncSession, business_id: uuid.UUID, new: NewBooking
) -> Booking | SlotConflict | KeyConflict:
    """Inserts a `pending` row inside its own savepoint, so a lost race leaves the
    session usable. Holds the business's reservation lock until the caller commits."""
    await lock_reservations(session, business_id)
    booking = Booking(
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
    )
    try:
        async with session.begin_nested():
            session.add(booking)
            await session.flush()
    except IntegrityError as exc:
        detail = str(exc.orig)
        if OVERLAP_CONSTRAINT in detail:
            return SlotConflict()
        if KEY_INDEX in detail:
            return KeyConflict()
        raise
    return booking


async def resolve_pending(
    session: AsyncSession,
    business_id: uuid.UUID,
    booking_id: uuid.UUID,
    *,
    status: str,
    calendar_event_id: str | None = None,
    failure_reason: str | None = None,
) -> Booking | None:
    """Moves a `pending` booking to `status`. None when it isn't pending any more
    (or isn't this business's): nothing is written."""
    values: dict[str, object] = {
        "status": status,
        "failure_reason": failure_reason,
        "updated_at": func.clock_timestamp(),
    }
    if calendar_event_id is not None:
        values["calendar_event_id"] = calendar_event_id
    stmt = (
        update(Booking)
        .where(Booking.business_id == business_id)
        .where(Booking.id == booking_id)
        .where(Booking.status == "pending")
        .values(**values)
        .returning(Booking)
    )
    result = await session.execute(stmt, execution_options={"populate_existing": True})
    return result.scalar_one_or_none()


async def expire_stale_pending(
    session: AsyncSession, business_id: uuid.UUID, older_than_seconds: int
) -> int:
    """`pending` rows older than the timeout belong to a request that died mid-booking:
    the Google event may or may not exist, so they become `needs_reconciliation` (which
    frees the slot here; Google's free/busy still shows the event if it was made).
    Database time, so an app clock pinned for evals can't expire fresh rows."""
    await lock_reservations(session, business_id)
    stmt = (
        update(Booking)
        .where(Booking.business_id == business_id)
        .where(Booking.status == "pending")
        .where(
            Booking.created_at
            < func.clock_timestamp() - func.make_interval(0, 0, 0, 0, 0, 0, older_than_seconds)
        )
        .values(
            status="needs_reconciliation",
            failure_reason="pending_timeout",
            updated_at=func.clock_timestamp(),
        )
    )
    result = await session.execute(stmt)
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def list_for_interaction(
    session: AsyncSession, business_id: uuid.UUID, interaction_id: uuid.UUID
) -> Sequence[Booking]:
    """Oldest first; every status (the panel shows failed attempts too)."""
    result = await session.execute(
        business_scoped_select(Booking, business_id)
        .where(Booking.interaction_id == interaction_id)
        .order_by(Booking.created_at, Booking.id)
        .execution_options(populate_existing=True)
    )
    return result.scalars().all()


class SqlBookingStore:
    """`services.booking.BookingStore` over one session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_active_by_key(
        self, business_id: uuid.UUID, idempotency_key: str
    ) -> Booking | None:
        return await find_active_by_key(self._session, business_id, idempotency_key)

    async def reserve(
        self, business_id: uuid.UUID, new: NewBooking
    ) -> Booking | SlotConflict | KeyConflict:
        return await reserve(self._session, business_id, new)

    async def resolve_pending(
        self,
        business_id: uuid.UUID,
        booking_id: uuid.UUID,
        *,
        status: str,
        calendar_event_id: str | None = None,
        failure_reason: str | None = None,
    ) -> Booking | None:
        return await resolve_pending(
            self._session,
            business_id,
            booking_id,
            status=status,
            calendar_event_id=calendar_event_id,
            failure_reason=failure_reason,
        )

    async def expire_stale_pending(self, business_id: uuid.UUID, older_than_seconds: int) -> int:
        return await expire_stale_pending(self._session, business_id, older_than_seconds)

    async def list_for_interaction(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID
    ) -> Sequence[Booking]:
        return await list_for_interaction(self._session, business_id, interaction_id)

    async def checkpoint(self) -> None:
        """Commits, so a reservation is visible to every other caller before Google is
        called, and no transaction is open across the provider call."""
        await self._session.commit()

    @asynccontextmanager
    async def savepoint(self) -> AsyncIterator[None]:
        async with self._session.begin_nested():
            yield
