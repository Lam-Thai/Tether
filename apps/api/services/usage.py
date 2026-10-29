"""`UsageGuard`: the one gate in front of every paid or quota'd provider call.

    async with usage.metered(UsageKind.GEMINI_REQUEST, business_id=..., interaction_id=...) as m:
        reply = await llm.generate(...)
        m.record(tokens_in=..., tokens_out=...)

Behind that small surface:

- Limits come from settings (`core/usage.py` builds `UsageLimits`), each kind
  with a global and a per-business daily cap. A kind with no `DailyLimit` is
  metered but never blocked (billing, calendar, tokens).
- The daily window is the UTC calendar day for every scope, global and
  per-business alike. One window keeps the global cap and every tenant's cap
  in step and needs no per-business timezone math; the panel shows the reset
  time in the viewer's local time.
- Race-safe counting: the reservation happens *before* the provider call, in
  the store's own short transaction (a conditional upsert per counter, global
  first, then business). Concurrent callers at the boundary can never push a
  counter past its limit, and no transaction is held across the call.
- A reserved request stays counted whether the call succeeds or fails: the
  provider saw it. A failure also records `failed_request`. Recording after
  the call is best effort (logged, never raised), so a bookkeeping hiccup
  never turns a good reply into an error; the reservation already enforced
  the limit.
- `LimitExceededError` is raised before any provider call. Callers answer
  with their safe fallback and must not retry it.
- If the store itself fails while reserving, a capped kind fails closed with
  `UsageUnavailableError` (no provider call; callers treat it like a provider
  outage), while a metered-only kind fails open (the call proceeds, the gap
  is logged): there is no limit to protect, so bookkeeping must not block
  checkout or webhooks.
- Estimated cost is computed from settings-driven rates even while a free
  tier makes the real bill zero (PRD §10.1).

Wrap `core.retry.retry_bounded` *around* `metered`, so each attempt is its
own reservation. No SQLAlchemy or FastAPI imports: the store is a port.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Protocol

from core.errors import AppError

logger = logging.getLogger("tether.usage")


class UsageKind(StrEnum):
    GEMINI_REQUEST = "gemini_request"
    GEMINI_TOKENS = "gemini_tokens"
    TTS_REQUEST = "tts_request"
    # One Gemini transcription of a caller's recording (VOICE_INPUT_MODE=gemini_audio).
    STT_REQUEST = "stt_request"
    CALL = "call"
    CALL_SECONDS = "call_seconds"
    SMS = "sms"
    CALENDAR_CALL = "calendar_call"
    BILLING_CALL = "billing_call"
    # Twilio REST calls that manage numbers (list, configure, verify): metered, never capped.
    TELEPHONY_API = "telephony_api"
    # Clerk Backend API calls (deleting a signed-in user on account deletion): metered, never
    # capped, and attributed to no business since the business is gone by then.
    IDENTITY_CALL = "identity_call"
    FAILED_REQUEST = "failed_request"


PROVIDER: Mapping[UsageKind, str] = {
    UsageKind.GEMINI_REQUEST: "gemini",
    UsageKind.GEMINI_TOKENS: "gemini",
    UsageKind.TTS_REQUEST: "gemini",
    UsageKind.STT_REQUEST: "gemini",
    UsageKind.CALL: "twilio",
    UsageKind.CALL_SECONDS: "twilio",
    UsageKind.SMS: "twilio",
    UsageKind.CALENDAR_CALL: "google_calendar",
    UsageKind.BILLING_CALL: "stripe",
    UsageKind.TELEPHONY_API: "twilio",
    UsageKind.IDENTITY_CALL: "clerk",
}

# Gemini requests whose tokens count toward `gemini_tokens` and the estimated cost (an
# audio transcription is billed in tokens like a chat turn).
TOKEN_KINDS = frozenset({UsageKind.GEMINI_REQUEST, UsageKind.STT_REQUEST})

# Bounds the best-effort bookkeeping after a call, so it can't stretch a deadline.
RECORD_TIMEOUT_SECONDS = 2.0

# The kinds the owner's Usage panel shows against a daily limit, in display order.
PANEL_KINDS = (
    UsageKind.GEMINI_REQUEST,
    UsageKind.TTS_REQUEST,
    UsageKind.STT_REQUEST,
    UsageKind.CALL,
    UsageKind.SMS,
)

LimitScope = Literal["global", "business"]
ReserveOutcome = Literal["ok", "global", "business"]


class LimitExceededError(AppError):
    """A daily limit is reached. Raised before the provider call; never retry it."""

    status_code = 429
    code = "usage_limit_reached"

    def __init__(self, kind: UsageKind, scope: LimitScope) -> None:
        super().__init__("The daily usage limit for this feature is reached.")
        self.kind = kind
        self.scope = scope


class UsageUnavailableError(AppError):
    """Limits couldn't be checked (store failure) for a capped kind. Fail closed."""

    status_code = 503
    code = "usage_unavailable"


class ConcurrencyLimitedError(AppError):
    status_code = 429
    code = "concurrency_limited"


@dataclass(frozen=True)
class DailyLimit:
    per_day: int  # all businesses together
    per_business_per_day: int


@dataclass(frozen=True)
class UsageLimits:
    daily: Mapping[UsageKind, DailyLimit]
    max_concurrent_interactions: int
    max_concurrent_interactions_per_business: int
    # An active interaction with no activity for this long stops counting as concurrent.
    interaction_idle_minutes: int
    # Per call; enforced by the voice slice, reported on the panel now.
    max_call_duration_seconds: int


@dataclass(frozen=True)
class CostRates:
    gemini_input_usd_per_million_tokens: Decimal
    gemini_output_usd_per_million_tokens: Decimal


@dataclass(frozen=True)
class UsageEvent:
    kind: UsageKind
    provider: str
    units: int
    estimated_cost: Decimal
    business_id: uuid.UUID | None
    interaction_id: uuid.UUID | None
    at: datetime


@dataclass(frozen=True)
class ActiveCounts:
    """Recently active interactions, across all businesses and for one business."""

    all_businesses: int
    business: int


class UsageStore(Protocol):
    async def reserve(
        self,
        event: UsageEvent,
        window: date,
        global_limit: int | None,
        business_limit: int | None,
    ) -> ReserveOutcome:
        """All or nothing, in one committed transaction: add `event.units` to the
        global counter (and the business counter when `event.business_id` is set)
        for `event.kind` on `window`, only while each stays within its limit
        (`None` = no limit), and insert `event`. Otherwise change nothing and
        say which scope refused."""
        ...

    async def record(self, events: Sequence[UsageEvent], window: date) -> None:
        """Insert `events` and add their units to the counters, unconditionally."""
        ...

    async def counters(self, business_id: uuid.UUID | None, window: date) -> Mapping[str, int]:
        """Counter values by kind for one business, or the global ones for `None`."""
        ...

    async def estimated_cost(
        self, business_id: uuid.UUID, start: datetime, end: datetime
    ) -> Decimal: ...

    async def active_interactions(
        self, business_id: uuid.UUID, idle_minutes: int
    ) -> ActiveCounts: ...


@dataclass
class Meter:
    """Handed to the body of `metered`; the body reports what the call consumed."""

    tokens_in: int = 0
    tokens_out: int = 0

    def record(self, *, tokens_in: int, tokens_out: int) -> None:
        self.tokens_in = max(0, tokens_in)
        self.tokens_out = max(0, tokens_out)


@dataclass(frozen=True)
class UsageWindow:
    day: date
    starts_at: datetime
    resets_at: datetime
    timezone: str = "UTC"


@dataclass(frozen=True)
class MeterView:
    kind: str
    used: int
    limit: int
    global_limit_reached: bool


@dataclass(frozen=True)
class ConcurrencyView:
    active: int
    limit: int
    idle_minutes: int
    global_limit_reached: bool


@dataclass(frozen=True)
class UsageSummary:
    window: UsageWindow
    meters: tuple[MeterView, ...]
    gemini_tokens: int
    failed_requests: int
    estimated_cost_usd: Decimal
    concurrency: ConcurrencyView
    max_call_duration_seconds: int


def _utcnow() -> datetime:
    return datetime.now(UTC)


class UsageGuard:
    def __init__(
        self,
        store: UsageStore,
        limits: UsageLimits,
        rates: CostRates,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._store = store
        self._limits = limits
        self._rates = rates
        self._clock = clock

    def window(self) -> UsageWindow:
        day = self._clock().astimezone(UTC).date()
        start = datetime.combine(day, time.min, tzinfo=UTC)
        return UsageWindow(day=day, starts_at=start, resets_at=start + timedelta(days=1))

    @asynccontextmanager
    async def metered(
        self,
        kind: UsageKind,
        *,
        business_id: uuid.UUID | None,
        interaction_id: uuid.UUID | None = None,
    ) -> AsyncIterator[Meter]:
        """Reserve one `kind` request, run the body, then record what it consumed.

        `business_id` always comes from `TenantContext` or a row resolved from it,
        never from a request. `None` meters globally only (e.g. a Stripe webhook).
        Raises `LimitExceededError` before the body runs when a limit is reached.
        """
        at = self._clock()
        window = self.window().day
        provider = PROVIDER[kind]
        self._refuse_zero_limits(kind, business_id)
        limit = self._limits.daily.get(kind)
        event = UsageEvent(kind, provider, 1, Decimal(0), business_id, interaction_id, at)
        try:
            outcome = await self._store.reserve(
                event,
                window,
                global_limit=limit.per_day if limit else None,
                business_limit=limit.per_business_per_day if limit and business_id else None,
            )
        except Exception as exc:
            if limit is not None:
                logger.exception("usage reservation failed", extra={"usage_kind": kind.value})
                raise UsageUnavailableError("Usage limits can't be checked right now.") from exc
            logger.exception("usage reservation failed (metered only, call proceeds)")
            outcome = "ok"
        if outcome != "ok":
            self._log_refusal(kind, outcome, business_id)
            raise LimitExceededError(kind, outcome)

        meter = Meter()
        try:
            yield meter
        except (Exception, asyncio.CancelledError):
            # The provider saw the attempt (or we gave up on it): it stays counted,
            # and whatever it consumed (e.g. tokens of a truncated reply) is kept.
            failed = UsageEvent(
                UsageKind.FAILED_REQUEST,
                provider,
                1,
                Decimal(0),
                business_id,
                interaction_id,
                self._clock(),
            )
            await self._record_quietly([failed, *self._consumption(kind, meter, event)], window)
            raise
        await self._record_quietly(self._consumption(kind, meter, event), window)

    def _consumption(self, kind: UsageKind, meter: Meter, request: UsageEvent) -> list[UsageEvent]:
        if kind not in TOKEN_KINDS or not (meter.tokens_in or meter.tokens_out):
            return []
        return [
            UsageEvent(
                UsageKind.GEMINI_TOKENS,
                request.provider,
                meter.tokens_in + meter.tokens_out,
                self._gemini_cost(meter),
                request.business_id,
                request.interaction_id,
                self._clock(),
            )
        ]

    async def record(
        self,
        kind: UsageKind,
        units: int,
        *,
        business_id: uuid.UUID | None,
        interaction_id: uuid.UUID | None = None,
    ) -> None:
        """Record consumption that happened outside a metered call (voice: a call's
        seconds, reported by Twilio when it ends). Never blocks and never raises; only for
        kinds with no daily limit, since nothing was reserved to enforce one."""
        if kind in self._limits.daily:
            raise ValueError(f"{kind.value} is capped: reserve it with metered()")
        if units <= 0:
            return
        event = UsageEvent(
            kind, PROVIDER[kind], units, Decimal(0), business_id, interaction_id, self._clock()
        )
        await self._record_quietly([event], self.window().day)

    def check_admission(self, active: ActiveCounts) -> None:
        """Refuse a new interaction when too many are active. Raises `ConcurrencyLimitedError`."""
        limits = self._limits
        if active.all_businesses >= limits.max_concurrent_interactions:
            scope: LimitScope = "global"
        elif active.business >= limits.max_concurrent_interactions_per_business:
            scope = "business"
        else:
            return
        logger.warning("interaction refused: concurrency limit", extra={"limit_scope": scope})
        raise ConcurrencyLimitedError(
            "Too many conversations are active right now. Try again in a few minutes."
        )

    @property
    def interaction_idle_minutes(self) -> int:
        return self._limits.interaction_idle_minutes

    async def summary(self, business_id: uuid.UUID) -> UsageSummary:
        window = self.window()
        mine = await self._store.counters(business_id, window.day)
        everyone = await self._store.counters(None, window.day)
        meters = []
        for kind in PANEL_KINDS:
            limit = self._limits.daily.get(kind)
            if limit is None:
                continue
            meters.append(
                MeterView(
                    kind=kind.value,
                    used=mine.get(kind.value, 0),
                    limit=limit.per_business_per_day,
                    # Never other tenants' numbers: only whether the global cap is used up.
                    global_limit_reached=everyone.get(kind.value, 0) >= limit.per_day,
                )
            )
        active = await self._store.active_interactions(
            business_id, self._limits.interaction_idle_minutes
        )
        return UsageSummary(
            window=window,
            meters=tuple(meters),
            gemini_tokens=mine.get(UsageKind.GEMINI_TOKENS.value, 0),
            failed_requests=mine.get(UsageKind.FAILED_REQUEST.value, 0),
            estimated_cost_usd=await self._store.estimated_cost(
                business_id, window.starts_at, window.resets_at
            ),
            concurrency=ConcurrencyView(
                active=active.business,
                limit=self._limits.max_concurrent_interactions_per_business,
                idle_minutes=self._limits.interaction_idle_minutes,
                global_limit_reached=active.all_businesses
                >= self._limits.max_concurrent_interactions,
            ),
            max_call_duration_seconds=self._limits.max_call_duration_seconds,
        )

    def _refuse_zero_limits(self, kind: UsageKind, business_id: uuid.UUID | None) -> None:
        # A zero limit can't be expressed as "increment while under the limit".
        limit = self._limits.daily.get(kind)
        if limit is None:
            return
        if limit.per_day <= 0:
            self._log_refusal(kind, "global", business_id)
            raise LimitExceededError(kind, "global")
        if business_id is not None and limit.per_business_per_day <= 0:
            self._log_refusal(kind, "business", business_id)
            raise LimitExceededError(kind, "business")

    def _gemini_cost(self, meter: Meter) -> Decimal:
        million = Decimal(1_000_000)
        return (
            Decimal(meter.tokens_in) * self._rates.gemini_input_usd_per_million_tokens
            + Decimal(meter.tokens_out) * self._rates.gemini_output_usd_per_million_tokens
        ) / million

    async def _record_quietly(self, events: Sequence[UsageEvent], window: date) -> None:
        if not events:
            return
        try:
            async with asyncio.timeout(RECORD_TIMEOUT_SECONDS):
                await self._store.record(events, window)
        except Exception:
            logger.exception("usage event could not be recorded")

    @staticmethod
    def _log_refusal(kind: UsageKind, scope: str, business_id: uuid.UUID | None) -> None:
        logger.warning(
            "usage limit reached",
            extra={
                "usage_kind": kind.value,
                "limit_scope": scope,
                "business_id": str(business_id) if business_id else None,
            },
        )
