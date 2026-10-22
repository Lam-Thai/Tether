"""Owner view of the weekly opening hours (PRD §8.5).

`get_week` returns the stored rows (Monday first) or none when the owner never saved
hours, which is not the same as closed. `replace_week` swaps all seven days in the
caller's transaction. Turning hours into instants is `services/directory`'s job.
No FastAPI or SQLAlchemy imports.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import time
from typing import Protocol

from core.tenant import Tenant
from models.hours import BusinessHours
from services.agent import require_business

DAYS_IN_WEEK = 7


@dataclass(frozen=True)
class DayWindow:
    day_of_week: int
    is_closed: bool
    opens_at: time | None
    closes_at: time | None


@dataclass(frozen=True)
class WeekHours:
    timezone: str
    # False until the owner first saves their hours; `days` is then empty.
    configured: bool
    days: Sequence[BusinessHours]


class HoursStore(Protocol):
    async def list_hours(self, business_id: uuid.UUID) -> Sequence[BusinessHours]: ...
    async def replace_week(
        self, business_id: uuid.UUID, days: Sequence[DayWindow]
    ) -> Sequence[BusinessHours]: ...


def _week(timezone: str, rows: Sequence[BusinessHours]) -> WeekHours:
    return WeekHours(timezone=timezone, configured=bool(rows), days=rows)


async def get_week(store: HoursStore, tenant: Tenant) -> WeekHours:
    business = require_business(tenant)
    return _week(business.timezone, await store.list_hours(business.id))


async def replace_week(store: HoursStore, tenant: Tenant, days: Sequence[DayWindow]) -> WeekHours:
    """`days` must hold exactly one window per weekday (0-6); the API schema checks the
    shape, this guards the invariant for any other caller."""
    if sorted(d.day_of_week for d in days) != list(range(DAYS_IN_WEEK)):
        raise ValueError("Hours must cover each day of the week exactly once.")
    business = require_business(tenant)
    ordered = sorted(days, key=lambda d: d.day_of_week)
    return _week(business.timezone, await store.replace_week(business.id, ordered))
