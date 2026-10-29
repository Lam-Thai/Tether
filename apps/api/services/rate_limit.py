"""Rate limiting (slice 17): `RateLimiter.hit(rule, subject) -> RateDecision`.

A fixed window aligned to `rule.window_seconds` (epoch based, so every process agrees on
the boundaries). The count lives behind the `RateLimitStore` port (Postgres in the app, a
dict in tests), never in this process: the API runs on serverless instances that share
nothing. The store's `increment` is atomic and refuses at the limit, so concurrent hits
never overshoot.

Fail open: if the store errors (database blip), the request is ALLOWED and a fixed code is
logged on `tether.rate_limit`. This guard protects cost and abuse, not correctness; turning
a database hiccup into a full outage of every sensitive endpoint (including Twilio's
webhooks mid-call) would be worse than a few unmetered requests. The provider quotas in
`UsageGuard` stay in force either way.
"""

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from core.errors import AppError

logger = logging.getLogger("tether.rate_limit")

# Windows older than this are dead weight; purging is opportunistic and bounded.
RETENTION = timedelta(days=1)
# Roughly one hit in this many also purges (keeps the table small without a scheduler).
PURGE_ONE_IN = 200


class RateLimitedError(AppError):
    """429 with a `Retry-After` header. The message never says which limit or subject."""

    status_code = 429
    code = "rate_limited"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Too many requests. Please wait a moment and try again.")
        self.retry_after_seconds = retry_after_seconds
        self.headers = {"Retry-After": str(retry_after_seconds)}


@dataclass(frozen=True)
class RateRule:
    name: str
    limit: int
    window_seconds: int


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    retry_after_seconds: int = 0


class RateLimitStore(Protocol):
    async def increment(self, key: str, window_start: datetime, limit: int) -> bool:
        """Count one hit in the window unless `limit` is reached. True if counted."""
        ...

    async def purge(self, before: datetime) -> None:
        """Delete (a bounded batch of) windows that started before `before`."""
        ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RateLimiter:
    def __init__(
        self,
        store: RateLimitStore,
        *,
        clock: Callable[[], datetime] = _utcnow,
        roll: Callable[[], float] = random.random,
    ) -> None:
        self._store = store
        self._clock = clock
        self._roll = roll

    async def hit(self, rule: RateRule, subject: str) -> RateDecision:
        now = self._clock()
        epoch = int(now.timestamp())
        start = epoch - epoch % rule.window_seconds
        retry_after = max(1, start + rule.window_seconds - epoch)
        window_start = datetime.fromtimestamp(start, UTC)
        try:
            allowed = await self._store.increment(
                f"{rule.name}:{subject}", window_start, rule.limit
            )
        except Exception as exc:
            # The type only: a database error's message can quote bound parameters.
            logger.error("rate_limit_store_failed", extra={"error_type": type(exc).__name__})
            return RateDecision(allowed=True)
        if self._roll() * PURGE_ONE_IN < 1:
            # Housekeeping only: a failed purge never changes the decision already made.
            try:
                await self._store.purge(now - RETENTION)
            except Exception as exc:
                logger.warning("rate_limit_purge_failed", extra={"error_type": type(exc).__name__})
        return RateDecision(allowed=True) if allowed else RateDecision(False, retry_after)
