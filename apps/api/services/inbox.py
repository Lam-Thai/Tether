"""The owner's inbox: list, detail and transcript of the business's interactions.

A read model. `Inbox` is the facade the router calls (one method per route); it
keeps the rules the owner relies on:

- Reading the list first sweeps this business's idle in-progress interactions to
  `abandoned` (template summary, zero AI calls), so "incomplete" is current.
- Reading a closed interaction whose summary is still missing summarizes it lazily;
  a failing summarizer never breaks the read.
- `from` / `to` are dates in the BUSINESS timezone, inclusive, converted to UTC
  instants here (DST safe), so a night booking shows under the owner's local day.
- Pagination is keyset over `(started_at DESC, id DESC)` with an opaque cursor, stable
  when several interactions start at the same instant.
- Stored text is redacted again on the way out (rows older than redaction).
- Operation labels are short machine strings built from the logged result, never free text.

No FastAPI or SQLAlchemy imports: the router passes an `InteractionQueries`
(`repositories/interaction_queries.py`) and the lifecycle.
"""

import base64
import binascii
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.errors import AppError
from core.tenant import Tenant
from services.agent import require_business
from services.escalation import EscalationRecord
from services.lifecycle import InteractionLifecycle, InteractionNotFoundError
from services.redaction import mask_phone, redact

TypeFilter = Literal["all", "bookings", "faqs", "escalations", "failed", "attention"]
DEFAULT_LIMIT = 25
MAX_LIMIT = 100
DEFAULT_MESSAGE_LIMIT = 50
MAX_MESSAGE_LIMIT = 200


class InvalidCursorError(AppError):
    status_code = 400
    code = "invalid_cursor"


class InvalidDateRangeError(AppError):
    status_code = 422
    code = "invalid_date_range"


@dataclass(frozen=True)
class InboxFilters:
    """What the owner asked for. `from_date`/`to_date` are business-local dates (inclusive);
    the `Inbox` fills `starts_at`/`ends_before` (UTC instants, half-open) from them."""

    type: TypeFilter = "all"
    status: str | None = None
    intent: str | None = None
    from_date: date | None = None
    to_date: date | None = None
    starts_at: datetime | None = None
    ends_before: datetime | None = None


@dataclass(frozen=True)
class Cursor:
    started_at: datetime
    id: uuid.UUID


def encode_cursor(cursor: Cursor) -> str:
    raw = f"{cursor.started_at.astimezone(UTC).isoformat()}|{cursor.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(token: str) -> Cursor:
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        stamp, _, ident = raw.partition("|")
        started_at = datetime.fromisoformat(stamp)
        if started_at.tzinfo is None:
            raise ValueError("naive cursor time")
        return Cursor(started_at.astimezone(UTC), uuid.UUID(ident))
    except (ValueError, OverflowError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidCursorError("The cursor is not valid.") from exc


@dataclass(frozen=True)
class Page[T]:
    items: tuple[T, ...]
    next_cursor: Cursor | None


@dataclass(frozen=True)
class EscalationBrief:
    """The escalation an inbox row shows: the open one with the highest priority,
    else the latest resolved one."""

    id: uuid.UUID
    reason: str
    urgency: str
    status: str
    sms_status: str


@dataclass(frozen=True)
class InteractionRow:
    id: uuid.UUID
    channel: str
    started_at: datetime
    ended_at: datetime | None
    caller: str | None
    intent: str | None
    summary: str | None
    summary_source: str | None
    status: str
    outcome: str | None
    requires_human: bool
    # Has an OPEN escalation (slice 13).
    needs_attention: bool = False
    escalation: EscalationBrief | None = None


@dataclass(frozen=True)
class OperationItem:
    id: uuid.UUID
    tool_name: str
    status: str
    created_at: datetime
    duration_ms: int
    # The logged machine summary; the facade turns it into `label`.
    result: Mapping[str, Any] = field(default_factory=dict)
    label: str | None = None


@dataclass(frozen=True)
class InteractionDetail:
    row: InteractionRow
    message_count: int
    operations: tuple[OperationItem, ...]
    escalations: tuple[EscalationRecord, ...] = ()


@dataclass(frozen=True)
class MessageItem:
    sequence_number: int
    speaker: str
    text: str
    created_at: datetime
    fallback: bool


@dataclass(frozen=True)
class MessagePage:
    items: tuple[MessageItem, ...]
    next_after: int | None


@dataclass(frozen=True)
class InboxPage:
    items: tuple[InteractionRow, ...]
    next_cursor: str | None  # opaque; pass it back as `cursor`
    timezone: str


@dataclass(frozen=True)
class DetailView:
    detail: InteractionDetail
    timezone: str


class InteractionQueries(Protocol):
    async def list(
        self, business_id: uuid.UUID, filters: InboxFilters, cursor: Cursor | None, limit: int
    ) -> Page[InteractionRow]: ...

    async def detail(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID
    ) -> InteractionDetail | None: ...

    async def messages(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID, after: int, limit: int
    ) -> MessagePage | None: ...


def day_window(
    from_date: date | None, to_date: date | None, timezone: str
) -> tuple[datetime | None, datetime | None]:
    """UTC instants [start of `from_date`, start of the day after `to_date`) in `timezone`."""
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")

    def start_of(day: date) -> datetime:
        return datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)

    starts_at = start_of(from_date) if from_date else None
    ends_before = start_of(to_date + timedelta(days=1)) if to_date else None
    return starts_at, ends_before


def operation_label(tool_name: str, status: str, result: Mapping[str, Any]) -> str | None:
    """A short machine label for the timeline, built only from fixed fields of the log."""
    outcome = result.get("result")
    reason = result.get("reason")
    if status != "succeeded":
        return _code(reason) or status
    if tool_name == "create_booking" and outcome == "confirmed":
        booking_id = str(result.get("booking_id") or "")[:8]
        label = f"booking {booking_id}" if booking_id else "booking confirmed"
        event_id = _code(result.get("event_id"))
        return f"{label} event {event_id}" if event_id else label
    if tool_name == "check_availability" and outcome == "available":
        return f"{int(result.get('offered', 0))} slots"
    if tool_name == "search_faq" and "count" in result:
        return f"{int(result['count'])} results"
    return _code(reason) or _code(outcome)


def _code(value: object) -> str | None:
    """Only short fixed codes pass: letters, digits and `_ - :`."""
    if not isinstance(value, str) or not value or len(value) > 40:
        return None
    return value if all(c.isalnum() or c in "_-:" for c in value) else None


class Inbox:
    def __init__(self, queries: InteractionQueries, lifecycle: InteractionLifecycle) -> None:
        self._queries = queries
        self._lifecycle = lifecycle

    async def list(
        self,
        tenant: Tenant,
        filters: InboxFilters,
        cursor: str | None,
        limit: int = DEFAULT_LIMIT,
    ) -> InboxPage:
        business = require_business(tenant)
        # Plain values: a rolled-back sweep expires the ORM objects behind `tenant`.
        business_id, timezone = business.id, business.timezone
        if filters.from_date and filters.to_date and filters.from_date > filters.to_date:
            raise InvalidDateRangeError("'from' must not be after 'to'.")
        decoded = decode_cursor(cursor) if cursor else None
        try:
            starts_at, ends_before = day_window(filters.from_date, filters.to_date, timezone)
        except OverflowError as exc:
            raise InvalidDateRangeError("The date range is out of bounds.") from exc
        scoped = replace(filters, starts_at=starts_at, ends_before=ends_before)
        await self._lifecycle.sweep_idle(business_id)
        page = await self._queries.list(business_id, scoped, decoded, max(1, min(limit, MAX_LIMIT)))
        return InboxPage(
            # Lists mask a caller ID to its last four digits; the detail view shows it.
            tuple(_masked(_redacted_row(row)) for row in page.items),
            encode_cursor(page.next_cursor) if page.next_cursor else None,
            timezone,
        )

    async def detail(self, tenant: Tenant, interaction_id: uuid.UUID) -> DetailView:
        business = require_business(tenant)
        # Plain values: a failed lazy summary rolls back, expiring the ORM objects.
        business_id, timezone = business.id, business.timezone
        found = await self._queries.detail(business_id, interaction_id)
        if found is None:
            raise InteractionNotFoundError("Interaction not found.")
        if found.row.status != "in_progress" and found.row.summary is None:
            await self._lifecycle.summarize_safely(business_id, interaction_id)
            found = await self._queries.detail(business_id, interaction_id) or found
        operations = tuple(
            replace(op, label=operation_label(op.tool_name, op.status, op.result))
            for op in found.operations
        )
        return DetailView(
            InteractionDetail(
                _redacted_row(found.row), found.message_count, operations, found.escalations
            ),
            timezone,
        )

    async def messages(
        self,
        tenant: Tenant,
        interaction_id: uuid.UUID,
        after: int = 0,
        limit: int = DEFAULT_MESSAGE_LIMIT,
    ) -> MessagePage:
        business = require_business(tenant)
        page = await self._queries.messages(
            business.id, interaction_id, max(0, after), max(1, min(limit, MAX_MESSAGE_LIMIT))
        )
        if page is None:
            raise InteractionNotFoundError("Interaction not found.")
        return MessagePage(
            tuple(replace(item, text=redact(item.text)) for item in page.items), page.next_after
        )


def _redacted_row(row: InteractionRow) -> InteractionRow:
    return replace(
        row,
        caller=redact(row.caller) if row.caller else None,
        summary=redact(row.summary) if row.summary else None,
    )


def _masked(row: InteractionRow) -> InteractionRow:
    return replace(row, caller=mask_phone(row.caller) if row.caller else None)
