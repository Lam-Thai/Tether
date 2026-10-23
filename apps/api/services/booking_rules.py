"""Owner-tunable booking rules (slice 10): slot interval, minimum notice, how far
ahead to book, and the buffer kept clear around busy blocks.

A business with no stored row uses the settings defaults (`customized=False`).
No FastAPI or SQLAlchemy imports.
"""

import uuid
from dataclasses import dataclass
from typing import Protocol

from core.tenant import Tenant
from services.agent import require_business


@dataclass(frozen=True)
class BookingRules:
    slot_interval_minutes: int
    min_notice_minutes: int
    max_days_ahead: int
    buffer_minutes: int


@dataclass(frozen=True)
class BookingRulesView:
    rules: BookingRules
    customized: bool  # False: no stored row, these are the defaults


class BookingRulesStore(Protocol):
    async def get_booking_rules(self, business_id: uuid.UUID) -> BookingRules | None: ...
    async def upsert_booking_rules(
        self, business_id: uuid.UUID, rules: BookingRules
    ) -> BookingRules: ...


async def get_rules(
    store: BookingRulesStore, tenant: Tenant, defaults: BookingRules
) -> BookingRulesView:
    business = require_business(tenant)
    stored = await store.get_booking_rules(business.id)
    if stored is None:
        return BookingRulesView(rules=defaults, customized=False)
    return BookingRulesView(rules=stored, customized=True)


async def replace_rules(
    store: BookingRulesStore, tenant: Tenant, rules: BookingRules
) -> BookingRulesView:
    business = require_business(tenant)
    saved = await store.upsert_booking_rules(business.id, rules)
    return BookingRulesView(rules=saved, customized=True)
