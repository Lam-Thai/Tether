"""`retry_bounded`: the one retry policy every provider integration uses.

No integration loops on its own. A caller states the attempt budget, the
backoff and which errors are worth another try; this module enforces a hard
ceiling on attempts so no configuration can make a retry unbounded.

Wrap `retry_bounded` *around* `UsageGuard.metered(...)`, never inside it, so
every attempt is reserved and counted against the limits on its own.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

# No policy may ask for more than this, whatever its settings say.
MAX_ATTEMPTS_CEILING = 5


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int
    # Delay before retry n (1-based) is `backoff_seconds * 2 ** (n - 1)`.
    backoff_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not 1 <= self.attempts <= MAX_ATTEMPTS_CEILING:
            raise ValueError(f"attempts must be between 1 and {MAX_ATTEMPTS_CEILING}.")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds cannot be negative.")


async def retry_bounded[T](
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    retry_if: Callable[[Exception], bool],
) -> T:
    """Call `fn` until it succeeds, an error fails `retry_if`, or attempts run out.

    The last error is re-raised unchanged. Cancellation is never retried.
    """
    for attempt in range(1, policy.attempts + 1):
        try:
            return await fn()
        except Exception as exc:
            if attempt == policy.attempts or not retry_if(exc):
                raise
            if policy.backoff_seconds:
                await asyncio.sleep(policy.backoff_seconds * 2 ** (attempt - 1))
    raise AssertionError("unreachable")  # pragma: no cover
