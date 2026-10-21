"""Race safety of the usage counters against a real Postgres."""

import asyncio
import uuid
from decimal import Decimal

from core.db import async_session_factory
from core.usage import SqlUsageStore
from repositories import usage as repo
from services.usage import UsageEvent, UsageKind

KIND = UsageKind.GEMINI_REQUEST


def _event(business_id: uuid.UUID, at) -> UsageEvent:
    return UsageEvent(KIND, "gemini", 1, Decimal(0), business_id, None, at)


async def test_parallel_reservations_at_the_boundary_never_exceed_either_limit(two_tenants, usage):
    tenant_a, tenant_b = two_tenants
    store = SqlUsageStore(async_session_factory)
    window = usage.day.date()

    async def reserve(business_id: uuid.UUID, business_limit: int, global_limit: int):
        return await store.reserve(
            _event(business_id, usage.day), window, global_limit, business_limit
        )

    # Per-business boundary: 12 racers for a limit of 5.
    outcomes = await asyncio.gather(*[reserve(tenant_a.business_id, 5, 1_000) for _ in range(12)])
    assert outcomes.count("ok") == 5
    assert set(outcomes) == {"ok", "business"}

    # Global boundary across both tenants: 5 already used, 3 left, 10 racers.
    outcomes = await asyncio.gather(
        *[reserve(t.business_id, 1_000, 8) for t in (tenant_a, tenant_b) for _ in range(5)]
    )
    assert outcomes.count("ok") == 3
    assert set(outcomes) == {"ok", "global"}

    async with async_session_factory() as session:
        mine = await repo.get_counters(session, tenant_a.business_id, window)
        theirs = await repo.get_counters(session, tenant_b.business_id, window)
        everyone = await repo.get_counters(session, None, window)
    assert everyone[KIND.value] == 8
    assert mine[KIND.value] + theirs.get(KIND.value, 0) == 8
    assert mine[KIND.value] >= 5  # refusals rolled back: nothing counted past a limit
