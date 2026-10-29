"""Deleting a business's test data (slice 16): "I played with it, now start clean".

`TestDataEraser.erase` removes the business's interactions (messages, operations,
escalations and voice turns go with them), bookings, usage events, usage counters of PAST
days and call-reply audio. It keeps the configuration (agent and its `active` flag,
services, FAQs, hours, booking rules, escalation settings, calendar connection, phone
numbers, subscription), cached fixed-line audio and TODAY's usage counters, so deleting test
data can never reset a daily cap.

Before the rows go, each confirmed booking's Google Calendar event is cancelled through
`CalendarConnections.cancel_event` (metered, commits before the call), best effort and under
an overall deadline: an event that is already gone counts as cancelled; a connection that
cannot be used (not selected, revoked) stops the attempts, since all of them would fail; any
other calendar error counts that one as not cancelled and carries on. No calendar error ever
leaves `erase`: the report says how many events could not be cancelled so the owner can
remove them in Google Calendar. The confirmation phrase is checked first, and nothing is
touched (no provider call, no delete) on a mismatch. The business always comes from the
tenant. No FastAPI or SQLAlchemy imports.
"""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from core.errors import AppError
from core.tenant import Tenant
from services.agent import require_business
from services.event_cancellation import DEADLINE_SECONDS, MAX_EVENT_CANCELS, cancel_events

CONFIRMATION_PHRASE = "DELETE TEST DATA"

logger = logging.getLogger("tether.test_data")


class ConfirmationMismatchError(AppError):
    status_code = 422
    code = "confirmation_mismatch"


@dataclass(frozen=True)
class ErasedCounts:
    interactions: int
    messages: int
    bookings: int
    escalations: int
    usage_events: int
    # Google event ids of the DELETED bookings that may still be on the calendar
    # (`confirmed` / `needs_reconciliation`), whenever they were made.
    calendar_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ErasureReport:
    interactions: int
    messages: int
    bookings: int
    escalations: int
    usage_events: int
    calendar_events_cancelled: int
    calendar_events_not_cancelled: int


class TestDataStore(Protocol):
    __test__ = False  # not a pytest class, despite the name

    async def calendar_event_ids(self, business_id: uuid.UUID) -> list[str]:
        """Google event ids of this business's `confirmed` / `needs_reconciliation`
        bookings, oldest first."""
        ...

    async def erase(self, business_id: uuid.UUID, *, keep_usage_from: date) -> ErasedCounts:
        """Deletes the business's test data in the caller's ONE transaction (usage counters
        only for days before `keep_usage_from`) and returns what was removed."""
        ...


class TestDataEraser:
    __test__ = False  # not a pytest class, despite the name

    def __init__(
        self,
        store: TestDataStore,
        cancel_event: Callable[[uuid.UUID, str], Awaitable[None]],
        today_utc: Callable[[], date],
        *,
        deadline_seconds: float = DEADLINE_SECONDS,
        max_cancels: int = MAX_EVENT_CANCELS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._cancel_event = cancel_event
        self._today_utc = today_utc
        self._deadline_seconds = deadline_seconds
        self._max_cancels = max_cancels
        self._monotonic = monotonic

    async def erase(self, tenant: Tenant, confirmation: str) -> ErasureReport:
        if confirmation.strip() != CONFIRMATION_PHRASE:
            raise ConfirmationMismatchError(f"Type {CONFIRMATION_PHRASE} to confirm.")
        business_id = require_business(tenant).id
        event_ids = await self._store.calendar_event_ids(business_id)
        cancelled = await cancel_events(
            self._cancel_event,
            business_id,
            event_ids,
            deadline_seconds=self._deadline_seconds,
            max_cancels=self._max_cancels,
            monotonic=self._monotonic,
        )
        counts = await self._store.erase(business_id, keep_usage_from=self._today_utc())
        # Judged on the rows actually deleted, so a booking confirmed after the snapshot
        # (nobody tried to cancel it) is reported as not cancelled, never silently dropped.
        not_cancelled = len(set(counts.calendar_event_ids) - cancelled)
        logger.info(
            "test data erased",
            extra={
                "business_id": str(business_id),
                "interactions": counts.interactions,
                "bookings": counts.bookings,
                "events_cancelled": len(cancelled),
                "events_not_cancelled": not_cancelled,
            },
        )
        return ErasureReport(
            interactions=counts.interactions,
            messages=counts.messages,
            bookings=counts.bookings,
            escalations=counts.escalations,
            usage_events=counts.usage_events,
            calendar_events_cancelled=len(cancelled),
            calendar_events_not_cancelled=not_cancelled,
        )
