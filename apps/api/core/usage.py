"""Builds the process's `UsageGuard`: limits and cost rates from settings, and
the Postgres `UsageStore` adapter over `repositories/usage.py`.

`SqlUsageStore` binds a session *factory*, not a request's session: each
operation runs and commits in its own short transaction, so a reservation is
visible to every other request at once and no transaction or lock is held
across a provider call. Tests override `get_usage_guard`.
"""

import uuid
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import Settings, settings
from core.db import async_session_factory
from repositories import interactions as interactions_repo
from repositories import usage as usage_repo
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


def usage_limits(config: Settings) -> UsageLimits:
    return UsageLimits(
        daily={
            UsageKind.GEMINI_REQUEST: DailyLimit(
                config.gemini_requests_per_day, config.gemini_requests_per_business_per_day
            ),
            UsageKind.TTS_REQUEST: DailyLimit(
                config.tts_requests_per_day, config.tts_requests_per_business_per_day
            ),
            UsageKind.STT_REQUEST: DailyLimit(
                config.stt_requests_per_day, config.stt_requests_per_business_per_day
            ),
            UsageKind.CALL: DailyLimit(config.calls_per_day, config.calls_per_business_per_day),
            UsageKind.SMS: DailyLimit(config.sms_per_day, config.sms_per_business_per_day),
        },
        max_concurrent_interactions=config.max_concurrent_interactions,
        max_concurrent_interactions_per_business=config.max_concurrent_interactions_per_business,
        interaction_idle_minutes=config.interaction_idle_minutes,
        max_call_duration_seconds=config.max_call_duration_seconds,
    )


def cost_rates(config: Settings) -> CostRates:
    return CostRates(
        gemini_input_usd_per_million_tokens=config.gemini_input_usd_per_million_tokens,
        gemini_output_usd_per_million_tokens=config.gemini_output_usd_per_million_tokens,
    )


def _add(session: AsyncSession, event: UsageEvent) -> None:
    usage_repo.add_event(
        session,
        business_id=event.business_id,
        interaction_id=event.interaction_id,
        provider=event.provider,
        event_type=event.kind.value,
        units=event.units,
        estimated_cost=event.estimated_cost,
        created_at=event.at,
    )


class SqlUsageStore:
    """`services.usage.UsageStore` over Postgres; one committed transaction per call."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def reserve(
        self,
        event: UsageEvent,
        window: date,
        global_limit: int | None,
        business_limit: int | None,
    ) -> ReserveOutcome:
        async with self._sessions() as session:
            outcome = await usage_repo.reserve(
                session,
                business_id=event.business_id,
                kind=event.kind.value,
                window=window,
                units=event.units,
                global_limit=global_limit,
                business_limit=business_limit,
            )
            if outcome != "ok":
                await session.rollback()
                return outcome
            _add(session, event)
            await session.commit()
            return "ok"

    async def record(self, events: Sequence[UsageEvent], window: date) -> None:
        async with self._sessions() as session:
            for event in events:
                await usage_repo.reserve(
                    session,
                    business_id=event.business_id,
                    kind=event.kind.value,
                    window=window,
                    units=event.units,
                    global_limit=None,
                    business_limit=None,
                )
                _add(session, event)
            await session.commit()

    async def counters(self, business_id: uuid.UUID | None, window: date) -> Mapping[str, int]:
        async with self._sessions() as session:
            return await usage_repo.get_counters(session, business_id, window)

    async def estimated_cost(
        self, business_id: uuid.UUID, start: datetime, end: datetime
    ) -> Decimal:
        async with self._sessions() as session:
            return await usage_repo.sum_estimated_cost(session, business_id, start, end)

    async def active_interactions(self, business_id: uuid.UUID, idle_minutes: int) -> ActiveCounts:
        async with self._sessions() as session:
            everyone, mine = await interactions_repo.count_recent_active(
                session, business_id, idle_minutes
            )
            return ActiveCounts(all_businesses=everyone, business=mine)


# One guard per process: it holds limits and a session factory, never usage state.
_guard = UsageGuard(
    SqlUsageStore(async_session_factory), usage_limits(settings), cost_rates(settings)
)


def get_usage_guard() -> UsageGuard:
    return _guard
