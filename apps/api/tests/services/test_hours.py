"""Weekly hours service against an in-memory store (no DB)."""

import uuid
from datetime import time
from types import SimpleNamespace

import pytest

from core.tenant import Tenant
from models.hours import BusinessHours
from services import hours as svc
from services.business import BusinessNotFoundError


class MemoryHoursStore:
    def __init__(self) -> None:
        self.rows: list[BusinessHours] = []

    async def list_hours(self, business_id):
        rows = [r for r in self.rows if r.business_id == business_id]
        return sorted(rows, key=lambda r: r.day_of_week)

    async def replace_week(self, business_id, days):
        self.rows = [r for r in self.rows if r.business_id != business_id]
        self.rows += [
            BusinessHours(
                business_id=business_id,
                day_of_week=d.day_of_week,
                is_closed=d.is_closed,
                opens_at=d.opens_at,
                closes_at=d.closes_at,
            )
            for d in days
        ]
        return await self.list_hours(business_id)


def _tenant() -> Tenant:
    business = SimpleNamespace(id=uuid.uuid4(), timezone="America/New_York")
    return Tenant(user=SimpleNamespace(), business=business)  # type: ignore[arg-type]


def _week(open_days=range(1, 6)) -> list[svc.DayWindow]:
    return [
        svc.DayWindow(d, False, time(9), time(17))
        if d in open_days
        else svc.DayWindow(d, True, None, None)
        for d in reversed(range(7))  # deliberately unordered
    ]


async def test_unset_then_replaced_then_overwritten_and_scoped():
    store, tenant, other = MemoryHoursStore(), _tenant(), _tenant()

    unset = await svc.get_week(store, tenant)
    assert (unset.configured, list(unset.days), unset.timezone) == (False, [], "America/New_York")

    saved = await svc.replace_week(store, tenant, _week())
    assert saved.configured and [d.day_of_week for d in saved.days] == list(range(7))
    assert [d.is_closed for d in saved.days] == [True, False, False, False, False, False, True]

    again = await svc.replace_week(store, tenant, _week(open_days=range(7)))
    assert len(store.rows) == 7 and not any(d.is_closed for d in again.days)
    assert (await svc.get_week(store, other)).configured is False


async def test_partial_or_duplicate_weeks_and_missing_business_are_rejected():
    store, tenant = MemoryHoursStore(), _tenant()
    with pytest.raises(ValueError):
        await svc.replace_week(store, tenant, _week()[:6])
    with pytest.raises(ValueError):
        await svc.replace_week(store, tenant, [*_week()[:6], svc.DayWindow(6, True, None, None)])
    assert store.rows == []
    with pytest.raises(BusinessNotFoundError):
        await svc.get_week(store, Tenant(user=SimpleNamespace(), business=None))  # type: ignore[arg-type]
