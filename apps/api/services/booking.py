"""`BookingService`: books one appointment, verified end to end, never twice.

One call, `book(tenant, BookingRequest) -> Confirmed | SlotTaken | Invalid | CalendarFailed`.
Only `Confirmed` means a booking exists; it is returned only after the Google event
was created AND the `bookings` row says `confirmed`. Behind it:

1. Validation: the customer's name (and phone, if given), and the service (active,
   this business's; through `AvailabilityService` / `BusinessDirectory`, never the raw
   tables). The duration always comes from the stored service.
2. Idempotency: the key is `interaction:service:start`. An active booking with that
   key is returned as it is (`Confirmed(replayed=True)`, no second event), or, while
   another call still holds it as `pending`, `CalendarFailed("in_progress")`.
3. Recheck: `AvailabilityService.find_slots` for that day right before creating
   anything (hours, notice, horizon, grid, Google free/busy). A start that isn't free
   is `SlotTaken` with the nearest free alternatives; a date the rules rule out is
   `Invalid(reason)`; a calendar that can't be read is `CalendarFailed`.
4. Concurrency: a `pending` row is inserted and COMMITTED before Google is called.
   Postgres's `ex_bookings_no_overlap` lets exactly one of two overlapping active
   bookings in (`models/booking.py`), so the loser gets `SlotTaken` and never reaches
   Google. No lock or transaction is held across the provider call. `pending` rows
   older than `PENDING_TIMEOUT_SECONDS` (a request that died mid-booking) are swept
   to `needs_reconciliation` first, so they can't block a slot forever.
5. Creation: `CalendarConnections.create_event`, keyed by the booking row's id, so the
   gateway's retries reuse one Google event id and can't create two events.
6. Persistence, then compensation: the row moves to `confirmed` with the event id.
   If Google failed, the row becomes `failed`; when the failure leaves it unclear
   whether Google created the event (a timeout), the event is deleted by its derived
   id first, and if even that can't be confirmed the row is `needs_reconciliation`.
   If the event was created but the row can't be confirmed, the event is cancelled
   (`failed`) or, if that fails too, the row is `needs_reconciliation`. Either way
   the customer is told nothing was booked.

Logs carry ids and fixed reason codes only, never customer text. No FastAPI or
SQLAlchemy imports: the store is a port (`repositories/bookings.SqlBookingStore`).
"""

import datetime as dt
import logging
import re
import uuid
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol, get_args
from zoneinfo import ZoneInfo

from core.tenant import Tenant
from integrations.google_calendar.port import CreatedEvent, EventDraft
from models.booking import Booking, KeyConflict, NewBooking, SlotConflict
from services.agent import require_business
from services.availability import (
    CalendarUnavailable,
    CalendarUnavailableReason,
    NoAvailability,
    NoAvailabilityReason,
    Slot,
    SlotOffer,
    SlotPreference,
)
from services.calendar import (
    CalendarAuthRevokedError,
    CalendarError,
    CalendarNotFoundError,
    CalendarNotSelectedError,
    CalendarUnavailableError,
    event_id_for,
)
from services.catalog import ServiceNotFoundError
from services.directory import BusinessDirectory, ServiceFact

logger = logging.getLogger("tether.booking")

# A `pending` booking older than this belongs to a request that died mid-booking. Far
# longer than one turn (`CONVERSATION_TURN_TIMEOUT_SECONDS`) plus Google's retries.
PENDING_TIMEOUT_SECONDS = 300
MAX_NAME_CHARS = 80
MAX_PHONE_CHARS = 32
_PHONE = re.compile(r"\+?[\d\s().-]+")
_MIN_PHONE_DIGITS, _MAX_PHONE_DIGITS = 7, 15

InvalidReason = Literal[
    "service_not_found",
    "missing_name",
    "invalid_phone",
    "past_date",
    "beyond_horizon",
    "closed",
    "hours_not_set",
    "service_too_long",
    "no_time_left",
    "too_soon",
]
CalendarFailedReason = Literal[
    # The calendar couldn't be used (`CalendarUnavailable` reasons).
    "not_connected",
    "reconnect_needed",
    "unavailable",
    # The same booking is being made by another call right now.
    "in_progress",
    # The event was created but the booking couldn't be saved (it was undone).
    "not_saved",
]
_DATE_RULE_REASONS: frozenset[str] = frozenset(get_args(NoAvailabilityReason)) - {"fully_booked"}


@dataclass(frozen=True)
class BookingRequest:
    interaction_id: uuid.UUID
    service_id: uuid.UUID
    # Local date and wall-clock start in the business's timezone.
    date: dt.date
    start_time: dt.time
    customer_name: str
    customer_phone: str | None = None


@dataclass(frozen=True)
class BookedAppointment:
    id: uuid.UUID
    service_id: uuid.UUID | None
    service_name: str
    # Aware, in the timezone the booking was made in (`bookings.timezone`).
    start: datetime
    end: datetime
    customer_name: str
    calendar_event_id: str


@dataclass(frozen=True)
class Confirmed:
    booking: BookedAppointment
    # True: this exact booking already existed (a retried call); nothing new was made.
    replayed: bool = False


@dataclass(frozen=True)
class SlotTaken:
    service: ServiceFact
    date: dt.date
    timezone: str
    # Free starts nearest the one asked for (at most `availability.MAX_OFFERED`).
    alternatives: tuple[Slot, ...]


@dataclass(frozen=True)
class Invalid:
    reason: InvalidReason
    service: ServiceFact | None = None
    # The bookable date range, for `past_date` / `beyond_horizon` / `too_soon`.
    earliest: dt.date | None = None
    latest: dt.date | None = None


@dataclass(frozen=True)
class CalendarFailed:
    reason: CalendarFailedReason
    service: ServiceFact | None = None


BookingOutcome = Confirmed | SlotTaken | Invalid | CalendarFailed


@dataclass(frozen=True)
class BookingSummary:
    id: uuid.UUID
    service_name: str
    start: datetime
    end: datetime
    customer_name: str
    status: str
    calendar_event_id: str | None
    created_at: datetime


class SlotChecker(Protocol):
    """`services.availability.AvailabilityService`."""

    async def find_slots(
        self, tenant: Tenant, service_id: uuid.UUID, preference: SlotPreference
    ) -> SlotOffer | NoAvailability | CalendarUnavailable: ...


class BookingCalendar(Protocol):
    """`services.calendar.CalendarConnections`."""

    async def create_event(
        self, business_id: uuid.UUID, draft: EventDraft, idempotency_key: str
    ) -> CreatedEvent: ...

    async def cancel_event(self, business_id: uuid.UUID, event_id: str) -> None: ...


class BookingStore(Protocol):
    """`repositories.bookings.SqlBookingStore`. Every method is scoped to one business."""

    async def find_active_by_key(
        self, business_id: uuid.UUID, idempotency_key: str
    ) -> Booking | None: ...

    async def reserve(
        self, business_id: uuid.UUID, new: NewBooking
    ) -> Booking | SlotConflict | KeyConflict:
        """Inserts a `pending` row (not committed). A lost race is a value, not an error."""
        ...

    async def resolve_pending(
        self,
        business_id: uuid.UUID,
        booking_id: uuid.UUID,
        *,
        status: str,
        calendar_event_id: str | None = None,
        failure_reason: str | None = None,
    ) -> Booking | None:
        """None when the row isn't `pending` any more; nothing written then."""
        ...

    async def expire_stale_pending(
        self, business_id: uuid.UUID, older_than_seconds: int
    ) -> int: ...

    async def list_for_interaction(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID
    ) -> Sequence[Booking]: ...

    async def checkpoint(self) -> None: ...

    def savepoint(self) -> AbstractAsyncContextManager[Any]: ...


class _NotSaved(Exception):
    """The confirmed status couldn't be written (the row moved on underneath)."""


class BookingService:
    def __init__(
        self,
        directory: BusinessDirectory,
        availability: SlotChecker,
        calendar: BookingCalendar,
        store: BookingStore,
    ) -> None:
        self._directory = directory
        self._availability = availability
        self._calendar = calendar
        self._store = store

    async def book(self, tenant: Tenant, request: BookingRequest) -> BookingOutcome:
        business_id = require_business(tenant).id
        name = " ".join(request.customer_name.split())
        if not name or len(name) > MAX_NAME_CHARS:
            return Invalid("missing_name")
        phone = _phone(request.customer_phone)
        if phone is False:
            return Invalid("invalid_phone")

        async with self._store.savepoint():
            profile = self._directory.profile(tenant)
            rules = await self._directory.booking_rules(tenant)
        zone = ZoneInfo(profile.timezone)
        start = _local_instant(request.date, request.start_time, zone)
        key = f"{request.interaction_id}:{request.service_id}:{start.astimezone(UTC).isoformat()}"

        async with self._store.savepoint():
            existing = await self._store.find_active_by_key(business_id, key)
        if existing is not None:
            if existing.status == "confirmed":
                _log("replayed", business_id, existing.id)
                return Confirmed(_appointment(existing), replayed=True)
            return CalendarFailed("in_progress")

        # The recheck: everything `check_availability` verifies, right before creating.
        try:
            found = await self._availability.find_slots(
                tenant,
                request.service_id,
                SlotPreference(request.date, around=start.time().replace(tzinfo=None)),
            )
        except ServiceNotFoundError:
            return Invalid("service_not_found")
        if isinstance(found, CalendarUnavailable):
            return CalendarFailed(found.reason, found.service)
        if isinstance(found, NoAvailability):
            if found.reason in _DATE_RULE_REASONS:
                return Invalid(found.reason, found.service, found.earliest, found.latest)  # type: ignore[arg-type]
            return SlotTaken(found.service, found.date, found.timezone, ())
        slot = next((s for s in found.slots if s.start == start), None)
        if slot is None:
            return SlotTaken(found.service, found.date, found.timezone, found.offered)
        service = found.service

        # Reserve (committed before Google is called, so every other caller sees it).
        new = NewBooking(
            interaction_id=request.interaction_id,
            service_id=service.id,
            service_name=service.name,
            timezone=profile.timezone,
            customer_name=name,
            customer_phone=phone or None,
            start_time=slot.start,
            end_time=slot.end,
            blocked_until=slot.end + timedelta(minutes=rules.buffer_minutes),
            idempotency_key=key,
        )
        async with self._store.savepoint():
            await self._store.expire_stale_pending(business_id, PENDING_TIMEOUT_SECONDS)
            reserved = await self._store.reserve(business_id, new)
        await self._store.checkpoint()
        if isinstance(reserved, SlotConflict):
            _log("slot_taken", business_id, None)
            others = tuple(s for s in found.offered if s.start != slot.start)
            return SlotTaken(service, found.date, found.timezone, others)
        if isinstance(reserved, KeyConflict):
            return CalendarFailed("in_progress", service)

        draft = EventDraft(
            summary=f"{service.name} - {name}",
            start=slot.start,
            end=slot.end,
            description=_description(phone or None, reserved.id),
            time_zone=profile.timezone,
        )
        event_key = str(reserved.id)
        try:
            event = await self._calendar.create_event(business_id, draft, event_key)
        except CalendarError as exc:
            reason = _calendar_reason(exc)
            status = "failed"
            if _may_have_created(exc):
                status = await self._undo(business_id, event_id_for(business_id, event_key))
            await self._settle(business_id, reserved.id, status, reason)
            _log(f"calendar_{reason}", business_id, reserved.id)
            return CalendarFailed(reason, service)

        try:
            async with self._store.savepoint():
                confirmed = await self._store.resolve_pending(
                    business_id, reserved.id, status="confirmed", calendar_event_id=event.id
                )
            if confirmed is None:
                raise _NotSaved
            await self._store.checkpoint()
        except Exception as exc:
            # The event exists but the booking doesn't say so: undo it, never confirm.
            logger.warning(
                "booking not saved after the event was created",
                extra={"booking_id": str(reserved.id), "error_type": type(exc).__name__},
            )
            status = await self._undo(business_id, event.id)
            await self._settle(business_id, reserved.id, status, "not_saved")
            return CalendarFailed("not_saved", service)

        _log("confirmed", business_id, confirmed.id)
        return Confirmed(_appointment(confirmed))

    async def for_interaction(
        self, tenant: Tenant, interaction_id: uuid.UUID
    ) -> list[BookingSummary]:
        """Every booking attempt this conversation made, oldest first (the test chat's
        side panel), in the local time each was made in. Scoped to the tenant's business;
        another business's id is []."""
        business_id = require_business(tenant).id
        rows = await self._store.list_for_interaction(business_id, interaction_id)
        return [_summary(row) for row in rows]

    async def _undo(self, business_id: uuid.UUID, event_id: str) -> str:
        """Deletes an event that may exist. `failed` once Google confirms it's gone,
        `needs_reconciliation` when that can't be confirmed."""
        try:
            await self._calendar.cancel_event(business_id, event_id)
        except CalendarNotFoundError:
            return "failed"  # never created (or already gone)
        except Exception as exc:
            logger.warning(
                "booking event could not be undone",
                extra={"business_id": str(business_id), "error_type": type(exc).__name__},
            )
            return "needs_reconciliation"
        return "failed"

    async def _settle(
        self, business_id: uuid.UUID, booking_id: uuid.UUID, status: str, reason: str
    ) -> None:
        """Best effort: a row that can't be moved now is swept later (stale pending)."""
        try:
            async with self._store.savepoint():
                await self._store.resolve_pending(
                    business_id, booking_id, status=status, failure_reason=reason
                )
            await self._store.checkpoint()
        except Exception as exc:
            logger.warning(
                "booking status not recorded",
                extra={"booking_id": str(booking_id), "error_type": type(exc).__name__},
            )


def _summary(row: Booking) -> BookingSummary:
    zone = ZoneInfo(row.timezone)
    return BookingSummary(
        id=row.id,
        service_name=row.service_name,
        start=row.start_time.astimezone(zone),
        end=row.end_time.astimezone(zone),
        customer_name=row.customer_name,
        status=row.status,
        calendar_event_id=row.calendar_event_id,
        created_at=row.created_at,
    )


def _appointment(row: Booking) -> BookedAppointment:
    zone = ZoneInfo(row.timezone)
    assert row.calendar_event_id is not None  # a confirmed row always has one (CHECK)
    return BookedAppointment(
        id=row.id,
        service_id=row.service_id,
        service_name=row.service_name,
        start=row.start_time.astimezone(zone),
        end=row.end_time.astimezone(zone),
        customer_name=row.customer_name,
        calendar_event_id=row.calendar_event_id,
    )


def _local_instant(day: dt.date, wall: dt.time, zone: ZoneInfo) -> datetime:
    """The instant of a local wall-clock time, round-tripped through UTC like the hours
    (a time inside the spring-forward gap moves to one that exists)."""
    naive = datetime.combine(day, wall.replace(tzinfo=None, second=0, microsecond=0))
    return naive.replace(tzinfo=zone).astimezone(UTC).astimezone(zone)


def _phone(value: str | None) -> str | None | Literal[False]:
    """Normalized phone, None when not given, False when it isn't a phone number."""
    if value is None or not value.strip():
        return None
    text = " ".join(value.split())
    digits = sum(ch.isdigit() for ch in text)
    if (
        len(text) > MAX_PHONE_CHARS
        or not _PHONE.fullmatch(text)
        or not _MIN_PHONE_DIGITS <= digits <= _MAX_PHONE_DIGITS
    ):
        return False
    return text


def _description(phone: str | None, booking_id: uuid.UUID) -> str:
    lines = ["Booked by the Tether receptionist."]
    if phone:
        lines.append(f"Phone: {phone}")
    lines.append(f"Booking id: {booking_id}")
    return "\n".join(lines)


def _calendar_reason(exc: CalendarError) -> CalendarUnavailableReason:
    if isinstance(exc, CalendarAuthRevokedError):
        return "reconnect_needed"
    if isinstance(exc, CalendarNotSelectedError):
        return "not_connected"
    return "unavailable"


def _may_have_created(exc: CalendarError) -> bool:
    """Only a provider failure after the request was sent (a timeout, a 5xx) leaves it
    unknown whether Google created the event. A reached usage limit made no call, and a
    revoked grant or a missing calendar was refused before anything was created."""
    return isinstance(exc, CalendarUnavailableError) and exc.reason != "usage"


def _log(outcome: str, business_id: uuid.UUID, booking_id: uuid.UUID | None) -> None:
    logger.info(
        "booking",
        extra={
            "outcome": outcome,
            "business_id": str(business_id),
            "booking_id": str(booking_id) if booking_id else None,
        },
    )
