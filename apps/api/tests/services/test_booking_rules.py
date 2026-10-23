"""Booking rules service against an in-memory store (no DB)."""

import uuid
from types import SimpleNamespace

from core.tenant import Tenant
from services import booking_rules as svc
from services.booking_rules import BookingRules

DEFAULTS = BookingRules(15, 120, 60, 0)


class MemoryBookingRulesStore:
    def __init__(self) -> None:
        self.rows: dict[uuid.UUID, BookingRules] = {}

    async def get_booking_rules(self, business_id):
        return self.rows.get(business_id)

    async def upsert_booking_rules(self, business_id, rules):
        self.rows[business_id] = rules
        return rules


def _tenant() -> Tenant:
    business = SimpleNamespace(id=uuid.uuid4(), timezone="America/New_York")
    return Tenant(user=SimpleNamespace(), business=business)  # type: ignore[arg-type]


async def test_defaults_until_saved_then_customized():
    store, tenant = MemoryBookingRulesStore(), _tenant()

    view = await svc.get_rules(store, tenant, DEFAULTS)
    assert view == svc.BookingRulesView(DEFAULTS, customized=False)

    mine = BookingRules(30, 0, 14, 10)
    saved = await svc.replace_rules(store, tenant, mine)
    assert saved == svc.BookingRulesView(mine, customized=True)
    assert await svc.get_rules(store, tenant, DEFAULTS) == saved


async def test_rules_are_per_business():
    store, a, b = MemoryBookingRulesStore(), _tenant(), _tenant()
    await svc.replace_rules(store, a, BookingRules(60, 30, 7, 5))
    assert (await svc.get_rules(store, b, DEFAULTS)).customized is False
