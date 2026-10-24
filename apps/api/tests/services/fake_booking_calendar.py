"""`FakeBookingCalendar`: stands in for `CalendarConnections` (free/busy + events) in unit
tests, per business. Created events are busy time, like Google's free/busy."""

import uuid
from dataclasses import dataclass, field
from datetime import date

from integrations.google_calendar.port import BusyInterval, CreatedEvent, EventDraft, TimeWindow
from services.calendar import CalendarNotFoundError, event_id_for
from tests.services.fake_busy_source import FakeBusySource


@dataclass
class FakeBookingCalendar:
    source: FakeBusySource = field(default_factory=FakeBusySource)
    # (business id, event id) -> event.
    events: dict[tuple[uuid.UUID, str], CreatedEvent] = field(default_factory=dict)
    drafts: list[EventDraft] = field(default_factory=list)
    # Raised by the next create / cancel while set (then cleared, unless `sticky`).
    create_error: Exception | None = None
    # Create the event at Google and THEN fail (a timeout after the request landed).
    create_lands_before_error: bool = False
    cancel_error: Exception | None = None
    created: int = 0
    cancelled: list[str] = field(default_factory=list)

    def add_busy(self, business_id: uuid.UUID, day: date, start: str, end: str) -> None:
        self.source.add(business_id, day, start, end)

    async def free_busy(self, business_id: uuid.UUID, window: TimeWindow) -> list[BusyInterval]:
        busy = await self.source.free_busy(business_id, window)
        events = [
            BusyInterval(e.start, e.end)
            for (owner, _), e in self.events.items()
            if owner == business_id and e.start < window.end and e.end > window.start
        ]
        return [*busy, *events]

    async def create_event(
        self, business_id: uuid.UUID, draft: EventDraft, idempotency_key: str
    ) -> CreatedEvent:
        event_id = event_id_for(business_id, idempotency_key)
        if self.create_error is not None and not self.create_lands_before_error:
            raise self.create_error
        self.drafts.append(draft)
        existing = self.events.get((business_id, event_id))
        if existing is None:
            self.created += 1
            existing = CreatedEvent(event_id, None, draft.start, draft.end)
            self.events[(business_id, event_id)] = existing
        if self.create_error is not None:
            raise self.create_error
        return existing

    async def cancel_event(self, business_id: uuid.UUID, event_id: str) -> None:
        if self.cancel_error is not None:
            raise self.cancel_error
        if self.events.pop((business_id, event_id), None) is None:
            raise CalendarNotFoundError("not_found")
        self.cancelled.append(event_id)

    def events_of(self, business_id: uuid.UUID) -> list[CreatedEvent]:
        return [e for (owner, _), e in self.events.items() if owner == business_id]
