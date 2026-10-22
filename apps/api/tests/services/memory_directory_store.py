"""In-memory `DirectoryStore` (services + weekly hours), scoped by business id like the SQL one."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from decimal import Decimal

from models.hours import BusinessHours
from models.service import Service

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class MemoryDirectoryStore:
    services: list[Service] = field(default_factory=list)
    hours: list[BusinessHours] = field(default_factory=list)
    reads: int = 0

    def add_service(
        self,
        business_id: uuid.UUID,
        name: str,
        *,
        duration_minutes: int = 30,
        price: str | None = None,
        description: str | None = None,
        active: bool = True,
    ) -> Service:
        service = Service(
            id=uuid.uuid4(),
            business_id=business_id,
            name=name,
            description=description,
            duration_minutes=duration_minutes,
            price=Decimal(price) if price is not None else None,
            active=active,
            created_at=_NOW,
            updated_at=_NOW,
        )
        self.services.append(service)
        return service

    def set_week(self, business_id: uuid.UUID, week: dict[int, tuple[str, str] | None]) -> None:
        """`week[day] = ("09:00", "17:00")` or `None` for closed; all 7 days required."""
        assert sorted(week) == list(range(7))
        self.hours = [h for h in self.hours if h.business_id != business_id]
        for day, window in week.items():
            self.hours.append(
                BusinessHours(
                    id=uuid.uuid4(),
                    business_id=business_id,
                    day_of_week=day,
                    is_closed=window is None,
                    opens_at=time.fromisoformat(window[0]) if window else None,
                    closes_at=time.fromisoformat(window[1]) if window else None,
                )
            )

    async def list_services(self, business_id: uuid.UUID, *, active_only: bool) -> list[Service]:
        self.reads += 1
        rows = [
            s
            for s in self.services
            if s.business_id == business_id and (s.active or not active_only)
        ]
        return sorted(rows, key=lambda s: (s.name.lower(), str(s.id)))

    async def list_hours(self, business_id: uuid.UUID) -> list[BusinessHours]:
        self.reads += 1
        return sorted(
            (h for h in self.hours if h.business_id == business_id), key=lambda h: h.day_of_week
        )
