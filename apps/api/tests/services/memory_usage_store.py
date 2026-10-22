"""In-memory `UsageStore` with the same contract as `SqlUsageStore`, for unit tests."""

import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

from services.usage import (
    ActiveCounts,
    CostRates,
    DailyLimit,
    ReserveOutcome,
    UsageEvent,
    UsageGuard,
    UsageKind,
    UsageLimits,
)


class MemoryUsageStore:
    def __init__(self) -> None:
        # Key: (business_id or None for global, kind, window day).
        self.counts: Counter[tuple[uuid.UUID | None, str, date]] = Counter()
        self.events: list[UsageEvent] = []
        self.active = ActiveCounts(all_businesses=0, business=0)
        self.fail_record = False
        self.fail_reserve = False

    async def reserve(
        self,
        event: UsageEvent,
        window: date,
        global_limit: int | None,
        business_limit: int | None,
    ) -> ReserveOutcome:
        if self.fail_reserve:
            raise RuntimeError("db down")
        global_key = (None, event.kind.value, window)
        if global_limit is not None and self.counts[global_key] + event.units > global_limit:
            return "global"
        business_key = (event.business_id, event.kind.value, window)
        if event.business_id is not None and business_limit is not None:
            if self.counts[business_key] + event.units > business_limit:
                return "business"
        self.counts[global_key] += event.units
        if event.business_id is not None:
            self.counts[business_key] += event.units
        self.events.append(event)
        return "ok"

    async def record(self, events: Sequence[UsageEvent], window: date) -> None:
        if self.fail_record:
            raise RuntimeError("db down")
        for event in events:
            self.counts[(None, event.kind.value, window)] += event.units
            if event.business_id is not None:
                self.counts[(event.business_id, event.kind.value, window)] += event.units
            self.events.append(event)

    async def counters(self, business_id, window):
        return {
            kind: n
            for (owner, kind, day), n in self.counts.items()
            if owner == business_id and day == window
        }

    async def estimated_cost(self, business_id, start, end):
        return sum(
            (
                e.estimated_cost
                for e in self.events
                if e.business_id == business_id and start <= e.at < end
            ),
            Decimal(0),
        )

    async def active_interactions(self, business_id, idle_minutes):
        return self.active

    def count(self, kind: UsageKind, business_id: uuid.UUID | None = None) -> int:
        """Today's (any day's) total for `kind` in one scope, for assertions."""
        return sum(
            n
            for (owner, k, _), n in self.counts.items()
            if owner == business_id and k == kind.value
        )


GENEROUS = DailyLimit(per_day=10_000, per_business_per_day=10_000)
RATES = CostRates(
    gemini_input_usd_per_million_tokens=Decimal("0.30"),
    gemini_output_usd_per_million_tokens=Decimal("2.50"),
)
NOW = datetime(2026, 10, 21, 15, 30, tzinfo=UTC)


def limits(**daily: DailyLimit) -> UsageLimits:
    """Generous limits everywhere, overridden per kind (`gemini_request=DailyLimit(...)`)."""
    base = {
        kind: GENEROUS
        for kind in (UsageKind.GEMINI_REQUEST, UsageKind.TTS_REQUEST, UsageKind.CALL, UsageKind.SMS)
    }
    base.update({UsageKind(name): limit for name, limit in daily.items()})
    return UsageLimits(
        daily=base,
        max_concurrent_interactions=100,
        max_concurrent_interactions_per_business=10,
        interaction_idle_minutes=15,
        max_call_duration_seconds=600,
    )


def guard(
    store: MemoryUsageStore | None = None,
    usage_limits: UsageLimits | None = None,
    clock=lambda: NOW,
) -> UsageGuard:
    return UsageGuard(store or MemoryUsageStore(), usage_limits or limits(), RATES, clock)
