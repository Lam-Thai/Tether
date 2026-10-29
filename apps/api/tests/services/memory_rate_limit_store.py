"""`MemoryRateLimitStore`: the `RateLimitStore` port in a dict, with the same rule as the
table (a window refuses at its limit). The real conditional upsert is tested against
Postgres in `tests/repositories/test_rate_limit.py`."""

from datetime import datetime


class MemoryRateLimitStore:
    def __init__(self) -> None:
        self.windows: dict[tuple[str, datetime], int] = {}
        self.purged_before: list[datetime] = []
        # Raised by the next `increment` (a database outage).
        self.error: Exception | None = None
        # Raised by every `purge`.
        self.purge_error: Exception | None = None

    async def increment(self, key: str, window_start: datetime, limit: int) -> bool:
        if self.error is not None:
            raise self.error
        count = self.windows.get((key, window_start), 0)
        if count >= limit:
            return False
        self.windows[(key, window_start)] = count + 1
        return True

    async def purge(self, before: datetime) -> None:
        if self.purge_error is not None:
            raise self.purge_error
        self.purged_before.append(before)
        self.windows = {k: v for k, v in self.windows.items() if k[1] >= before}
