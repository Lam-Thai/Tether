"""Dashboard numbers for the owner's home page (slice 16).

`DashboardMetrics.summary` counts the business's interactions, confirmed bookings and FAQ
answers inside an inclusive range of BUSINESS-LOCAL dates, plus the open escalations right
now (a queue, so not range-bound). The range becomes UTC instants `[start 00:00, end+1
00:00)` in the business timezone (DST safe, same conversion as the inbox filters). The
default range is the last 30 days ending today. Every count is a backend record; nothing
here comes from a model. No FastAPI or SQLAlchemy imports.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.tenant import Tenant
from services.agent import require_business
from services.inbox import InvalidDateRangeError, day_window

DEFAULT_DAYS = 30
MAX_DAYS = 366


@dataclass(frozen=True)
class DateRange:
    """Inclusive, business-local dates."""

    start: date
    end: date


@dataclass(frozen=True)
class MetricCounts:
    total_interactions: int
    successful_bookings: int
    faqs_answered: int
    open_escalations: int


@dataclass(frozen=True)
class Metrics:
    range: DateRange
    timezone: str
    total_interactions: int
    successful_bookings: int
    faqs_answered: int
    open_escalations: int


class MetricsQueries(Protocol):
    async def counts(
        self, business_id: uuid.UUID, starts_at: datetime, ends_before: datetime
    ) -> MetricCounts:
        """One business's counts for `[starts_at, ends_before)`; open escalations are
        counted as of now."""
        ...


def _local_today(clock: Callable[[], datetime], timezone: str) -> date:
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    return clock().astimezone(zone).date()


class DashboardMetrics:
    def __init__(
        self, queries: MetricsQueries, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._queries = queries
        self._clock = clock

    async def summary(
        self, tenant: Tenant, start: date | None = None, end: date | None = None
    ) -> Metrics:
        """Counts for the inclusive business-local range `start..end`. A missing `end` is
        today in the business timezone, a missing `start` is 29 days before `end` (so the
        default is the last 30 days). A bad range is 422 `invalid_date_range`."""
        business = require_business(tenant)
        business_id, timezone = business.id, business.timezone
        resolved = self._resolve_range(timezone, start, end)
        _validate_range(resolved)
        try:
            starts_at, ends_before = day_window(resolved.start, resolved.end, timezone)
        except OverflowError as exc:
            raise InvalidDateRangeError("The date range is out of bounds.") from exc
        assert starts_at is not None and ends_before is not None
        counts = await self._queries.counts(business_id, starts_at, ends_before)
        return Metrics(
            range=resolved,
            timezone=timezone,
            total_interactions=counts.total_interactions,
            successful_bookings=counts.successful_bookings,
            faqs_answered=counts.faqs_answered,
            open_escalations=counts.open_escalations,
        )

    def _resolve_range(self, timezone: str, start: date | None, end: date | None) -> DateRange:
        if end is None:
            end = _local_today(self._clock, timezone)
        if start is None:
            try:
                start = end - timedelta(days=DEFAULT_DAYS - 1)
            except OverflowError as exc:
                raise InvalidDateRangeError("The date range is out of bounds.") from exc
        return DateRange(start, end)


def _validate_range(value: DateRange) -> None:
    if value.start > value.end:
        raise InvalidDateRangeError("'from' must not be after 'to'.")
    if (value.end - value.start).days + 1 > MAX_DAYS:
        raise InvalidDateRangeError(f"The date range can be at most {MAX_DAYS} days.")
