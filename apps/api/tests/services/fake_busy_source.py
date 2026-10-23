"""`FakeBusySource`: stands in for `CalendarConnections.free_busy` in unit tests, per business."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from integrations.google_calendar.port import BusyInterval, TimeWindow

NEW_YORK = ZoneInfo("America/New_York")


def local(day: date, hh_mm: str, zone: ZoneInfo = NEW_YORK) -> datetime:
    """The UTC instant of a local wall time."""
    return datetime.combine(day, time.fromisoformat(hh_mm), tzinfo=zone).astimezone(UTC)


@dataclass
class FakeBusySource:
    busy: dict[uuid.UUID, list[BusyInterval]] = field(default_factory=dict)
    # Raised by every call while set (a `CalendarError` subclass in tests).
    error: Exception | None = None
    calls: list[tuple[uuid.UUID, TimeWindow]] = field(default_factory=list)

    def add(self, business_id: uuid.UUID, day: date, start: str, end: str) -> None:
        self.busy.setdefault(business_id, []).append(
            BusyInterval(local(day, start), local(day, end))
        )

    async def free_busy(self, business_id: uuid.UUID, window: TimeWindow) -> list[BusyInterval]:
        self.calls.append((business_id, window))
        if self.error is not None:
            raise self.error
        return [
            b
            for b in self.busy.get(business_id, [])
            if b.start < window.end and b.end > window.start
        ]
