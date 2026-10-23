"""`AvailabilityService`: real, bookable start times for one service on one local date.

Small interface, `find_slots(tenant, service_id, preference)`, returning one of three
explicit results:

- `SlotOffer`: every free start that day (`slots`) and a short, spread-out set to
  offer (`offered`, at most `MAX_OFFERED`), shaped by the customer's preference.
- `NoAvailability`: a fixed `reason` (past date, beyond the booking horizon, closed,
  hours not listed, the service is longer than the opening window, no time left
  today, inside the minimum notice, fully booked). Decided by the backend, never
  by the model.
- `CalendarUnavailable`: the calendar couldn't be read (`not_connected`,
  `reconnect_needed`, `unavailable`). No times at all: nothing was verified.

Behind it:

- Business facts come only from `BusinessDirectory` (the service must be active;
  hours are local wall-clock in the business timezone; booking rules fall back to
  the settings defaults). Busy time comes only from `CalendarAvailability`
  (`CalendarConnections.free_busy`: metered, retried, a revoked grant flips the
  connection to `reconnect_needed`).
- The core is the pure `compute_slots(windows, busy, duration, rules, now)`: DST
  safe (all arithmetic in UTC, results in the window's zone), busy blocks merged and
  widened by the buffer on both sides, starts snapped to the slot grid anchored at
  opening time, minimum notice and the booking horizon applied as instants.
  `choose_slots` picks the options: a part of the day (morning < 12:00 <= afternoon
  < 17:00 <= evening) and/or the slots nearest a wanted time, else evenly spread.
- Transactions: every DB read happens first, inside `savepoint()`, so a DB error
  rolls back only this lookup. The calendar source commits before it calls Google,
  so no transaction is held across the provider call. Calendar latency is logged.

No FastAPI or SQLAlchemy imports.
"""

import datetime as dt
import logging
import math
import time as monotonic
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal, Protocol

from core.tenant import Tenant
from integrations.google_calendar.port import BusyInterval, TimeWindow
from services.agent import require_business
from services.booking_rules import BookingRules
from services.calendar import (
    CalendarAuthRevokedError,
    CalendarError,
    CalendarNotSelectedError,
)
from services.catalog import ServiceNotFoundError
from services.directory import (
    BusinessDirectory,
    Closed,
    Found,
    HoursNotSet,
    Open,
    ServiceFact,
)

logger = logging.getLogger("tether.availability")

MAX_OFFERED = 3
# Parts of the day, by local start time.
AFTERNOON_STARTS = time(12, 0)
EVENING_STARTS = time(17, 0)
# A lookup slower than this is logged as a warning (the target is < 2 s).
SLOW_LOOKUP_MS = 2000

TimeOfDay = Literal["morning", "afternoon", "evening"]
NoAvailabilityReason = Literal[
    "past_date",
    "beyond_horizon",
    "closed",
    "hours_not_set",
    "service_too_long",
    "no_time_left",
    "too_soon",
    "fully_booked",
]
CalendarUnavailableReason = Literal["not_connected", "reconnect_needed", "unavailable"]


@dataclass(frozen=True)
class Slot:
    # Aware datetimes in the business's timezone; compare or subtract via UTC.
    start: datetime
    end: datetime


@dataclass(frozen=True)
class SlotPreference:
    date: dt.date
    time_of_day: TimeOfDay | None = None
    # A wanted local start time ("around 3pm"): the nearest slots are offered.
    around: time | None = None


@dataclass(frozen=True)
class SlotOffer:
    service: ServiceFact
    date: dt.date
    timezone: str
    # Every free start that day, in order.
    slots: tuple[Slot, ...]
    # What to offer: at most `MAX_OFFERED`, in order, all from `slots`.
    offered: tuple[Slot, ...]
    # False: nothing fit the preferred part of the day, so `offered` spans the whole day.
    matched_preference: bool


@dataclass(frozen=True)
class NoAvailability:
    service: ServiceFact
    date: dt.date
    timezone: str
    reason: NoAvailabilityReason
    # The bookable date range, for `past_date` / `beyond_horizon` / `too_soon`.
    earliest: dt.date | None = None
    latest: dt.date | None = None


@dataclass(frozen=True)
class CalendarUnavailable:
    service: ServiceFact
    date: dt.date
    timezone: str
    reason: CalendarUnavailableReason


AvailabilityResult = SlotOffer | NoAvailability | CalendarUnavailable


class CalendarAvailability(Protocol):
    """`services.calendar.CalendarConnections`."""

    async def free_busy(self, business_id: uuid.UUID, window: TimeWindow) -> list[BusyInterval]:
        """Busy blocks on the business's chosen calendar. Raises `CalendarNotSelectedError`
        (not configured, not connected, no calendar chosen), `CalendarAuthRevokedError`
        (reconnect needed) or another `CalendarError` (unavailable)."""
        ...


# --- the pure core -----------------------------------------------------------------


def compute_slots(
    windows: Sequence[Open],
    busy: Sequence[BusyInterval],
    duration: timedelta,
    rules: BookingRules,
    now: datetime,
) -> list[Slot]:
    """Every start time, across `windows`, at which `duration` fits inside the window
    without touching a busy block (widened by the buffer on both sides), on the slot
    grid anchored at the window's opening time, no earlier than `now` plus the minimum
    notice and no later than `now` plus the booking horizon."""
    if now.tzinfo is None:
        raise ValueError("`now` must be timezone-aware.")
    if duration <= timedelta(0):
        raise ValueError("A service needs a positive duration.")
    step = timedelta(minutes=rules.slot_interval_minutes)
    if step <= timedelta(0):
        raise ValueError("The slot interval must be positive.")
    buffer = timedelta(minutes=rules.buffer_minutes)
    earliest = now.astimezone(UTC) + timedelta(minutes=rules.min_notice_minutes)
    latest_start = now.astimezone(UTC) + timedelta(days=rules.max_days_ahead)
    blocks = _merged((b.start - buffer, b.end + buffer) for b in busy)

    slots: list[Slot] = []
    for window in windows:
        zone = window.start.tzinfo
        opens, closes = window.start.astimezone(UTC), window.end.astimezone(UTC)
        start = _snap(opens, max(opens, earliest), step)
        while start + duration <= closes and start <= latest_start:
            clash = next(
                (end for begin, end in blocks if begin < start + duration and end > start), None
            )
            if clash is not None:
                start = _snap(opens, clash, step)
                continue
            slots.append(Slot(start.astimezone(zone), (start + duration).astimezone(zone)))
            start += step
    return slots


def choose_slots(
    slots: Sequence[Slot], preference: SlotPreference, limit: int = MAX_OFFERED
) -> tuple[tuple[Slot, ...], bool]:
    """At most `limit` slots to offer, in order, and whether they match the preferred
    part of the day (False: none did, so these span the whole day)."""
    if not slots:
        return (), True
    pool = [s for s in slots if _part_of_day(s.start.time()) == preference.time_of_day]
    matched = preference.time_of_day is None or bool(pool)
    if preference.time_of_day is None or not pool:
        pool = list(slots)
    if preference.around is not None:
        wanted = _minutes(preference.around)
        nearest = sorted(pool, key=lambda s: (abs(_minutes(s.start.time()) - wanted), s.start))
        chosen = sorted(nearest[:limit], key=lambda s: s.start.astimezone(UTC))
        return tuple(chosen), matched
    if len(pool) <= limit:
        return tuple(pool), matched
    if limit == 1:
        return (pool[0],), matched
    picks = sorted({round(i * (len(pool) - 1) / (limit - 1)) for i in range(limit)})
    return tuple(pool[i] for i in picks), matched


def _merged(blocks: Any) -> list[tuple[datetime, datetime]]:
    merged: list[tuple[datetime, datetime]] = []
    for begin, end in sorted((b.astimezone(UTC), e.astimezone(UTC)) for b, e in blocks):
        if merged and begin <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((begin, end))
    return merged


def _snap(anchor: datetime, moment: datetime, step: timedelta) -> datetime:
    """The first grid point (`anchor` + n * `step`) at or after `moment`."""
    if moment <= anchor:
        return anchor
    return anchor + step * math.ceil((moment - anchor) / step)


def _part_of_day(wall: time) -> TimeOfDay:
    if wall < AFTERNOON_STARTS:
        return "morning"
    return "afternoon" if wall < EVENING_STARTS else "evening"


def _minutes(wall: time) -> int:
    return wall.hour * 60 + wall.minute


# --- the service -------------------------------------------------------------------


class AvailabilityService:
    def __init__(
        self,
        directory: BusinessDirectory,
        calendar: CalendarAvailability,
        *,
        savepoint: Callable[[], AbstractAsyncContextManager[Any]] = nullcontext,
    ) -> None:
        self._directory = directory
        self._calendar = calendar
        self._savepoint = savepoint

    async def find_slots(
        self, tenant: Tenant, service_id: uuid.UUID, preference: SlotPreference
    ) -> AvailabilityResult:
        """Raises `ServiceNotFoundError` for an id that isn't one of this business's
        active services; everything else is an explicit result."""
        async with self._savepoint():
            match = await self._directory.find_service(tenant, str(service_id))
            if not isinstance(match, Found):
                raise ServiceNotFoundError("Service not found.")
            service = match.service
            profile = self._directory.profile(tenant)
            rules = await self._directory.booking_rules(tenant)
            now = self._directory.now(tenant)
            window = await self._directory.opening_window(tenant, preference.date)

        today = now.date()
        latest = today + timedelta(days=rules.max_days_ahead)

        def none(reason: NoAvailabilityReason, **extra: date) -> NoAvailability:
            return NoAvailability(service, preference.date, profile.timezone, reason, **extra)

        if preference.date < today:
            return none("past_date", earliest=today, latest=latest)
        if preference.date > latest:
            return none("beyond_horizon", earliest=today, latest=latest)
        if isinstance(window, Closed):
            return none("closed")
        if isinstance(window, HoursNotSet):
            return none("hours_not_set")
        duration = timedelta(minutes=service.duration_minutes)
        if window.minutes < service.duration_minutes:
            return none("service_too_long")
        if not compute_slots([window], [], duration, rules, now):
            # Cut off by the minimum notice or the horizon (its last day), before any
            # calendar call.
            if preference.date == today:
                return none("no_time_left")
            soonest = (now + timedelta(minutes=rules.min_notice_minutes)).date()
            if preference.date <= soonest:
                return none("too_soon", earliest=soonest, latest=latest)
            return none("beyond_horizon", earliest=today, latest=latest)

        buffer = timedelta(minutes=rules.buffer_minutes)
        span = TimeWindow(window.start - buffer, window.end + buffer)
        business_id = require_business(tenant).id
        started = monotonic.perf_counter()
        failure: CalendarUnavailableReason | None = None
        busy: list[BusyInterval] = []
        try:
            busy = await self._calendar.free_busy(business_id, span)
        except CalendarNotSelectedError:
            failure = "not_connected"
        except CalendarAuthRevokedError:
            failure = "reconnect_needed"
        except CalendarError:
            failure = "unavailable"
        finally:
            _log_latency(business_id, started, failure or "ok")
        if failure is not None:
            return CalendarUnavailable(service, preference.date, profile.timezone, failure)

        slots = compute_slots([window], busy, duration, rules, now)
        if not slots:
            return none("fully_booked")
        offered, matched = choose_slots(slots, preference)
        return SlotOffer(
            service=service,
            date=preference.date,
            timezone=profile.timezone,
            slots=tuple(slots),
            offered=offered,
            matched_preference=matched,
        )


def _log_latency(business_id: uuid.UUID, started: float, outcome: str) -> None:
    elapsed_ms = round((monotonic.perf_counter() - started) * 1000)
    extra = {"business_id": str(business_id), "duration_ms": elapsed_ms, "outcome": outcome}
    if elapsed_ms > SLOW_LOOKUP_MS:
        logger.warning("calendar availability lookup slow", extra=extra)
    else:
        logger.info("calendar availability lookup", extra=extra)
