"""`RateLimiter` on an in-memory store: windows, retry_after, subjects, fail-open, purge."""

import logging
from datetime import UTC, datetime, timedelta

from services.rate_limit import RETENTION, RateLimitedError, RateLimiter, RateRule
from tests.services.memory_rate_limit_store import MemoryRateLimitStore

RULE = RateRule("probe", limit=3, window_seconds=60)
T0 = datetime(2026, 1, 5, 12, 0, 10, tzinfo=UTC)  # 10 s into a minute


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _limiter(store=None, clock=None, roll=lambda: 0.99):
    store = store or MemoryRateLimitStore()
    clock = clock or Clock(T0)
    return RateLimiter(store, clock=clock, roll=roll), store, clock


async def test_allows_up_to_the_limit_then_refuses_with_the_time_left():
    limiter, _, _ = _limiter()

    decisions = [await limiter.hit(RULE, "biz") for _ in range(4)]

    assert [d.allowed for d in decisions] == [True, True, True, False]
    assert decisions[3].retry_after_seconds == 50  # the window ends at :00 of the next minute


async def test_the_next_window_starts_a_fresh_count():
    limiter, _, clock = _limiter()
    for _ in range(4):
        await limiter.hit(RULE, "biz")

    clock.now = T0 + timedelta(seconds=50)  # exactly on the boundary

    assert (await limiter.hit(RULE, "biz")).allowed is True


async def test_retry_after_is_at_least_one_second_on_the_last_second():
    limiter, _, clock = _limiter()
    clock.now = datetime(2026, 1, 5, 12, 0, 59, 900000, tzinfo=UTC)
    for _ in range(3):
        await limiter.hit(RULE, "biz")

    assert (await limiter.hit(RULE, "biz")).retry_after_seconds == 1


async def test_subjects_and_rules_are_counted_separately():
    limiter, _, _ = _limiter()
    other_rule = RateRule("other", limit=3, window_seconds=60)
    for _ in range(3):
        await limiter.hit(RULE, "a")

    assert (await limiter.hit(RULE, "a")).allowed is False
    assert (await limiter.hit(RULE, "b")).allowed is True
    assert (await limiter.hit(other_rule, "a")).allowed is True


async def test_a_limit_of_zero_blocks_everything():
    limiter, _, _ = _limiter()

    assert (await limiter.hit(RateRule("off", 0, 60), "a")).allowed is False


async def test_a_failing_store_fails_open_and_logs_a_code_only(caplog):
    limiter, store, _ = _limiter()
    store.error = RuntimeError("connection to db failed: password=hunter2")

    with caplog.at_level(logging.ERROR, logger="tether.rate_limit"):
        decision = await limiter.hit(RULE, "biz")

    assert decision.allowed is True
    assert [r.getMessage() for r in caplog.records] == ["rate_limit_store_failed"]
    assert "hunter2" not in caplog.text


async def test_purges_expired_windows_only_when_the_roll_comes_up():
    limiter, store, _ = _limiter(roll=lambda: 0.0)

    await limiter.hit(RULE, "biz")

    assert store.purged_before == [T0 - RETENTION]
    quiet, quiet_store, _ = _limiter(roll=lambda: 0.5)
    await quiet.hit(RULE, "biz")
    assert quiet_store.purged_before == []


async def test_a_failing_purge_never_turns_a_refusal_into_an_allow(caplog):
    limiter, store, _ = _limiter(roll=lambda: 0.0)
    store.purge_error = RuntimeError("deadlock detected")
    for _ in range(3):
        await limiter.hit(RULE, "biz")

    with caplog.at_level(logging.WARNING, logger="tether.rate_limit"):
        decision = await limiter.hit(RULE, "biz")

    assert decision.allowed is False
    assert decision.retry_after_seconds == 50
    assert "rate_limit_purge_failed" in [r.getMessage() for r in caplog.records]


def test_the_error_carries_retry_after_for_the_handler():
    error = RateLimitedError(7)

    assert (error.status_code, error.code) == (429, "rate_limited")
    assert error.headers == {"Retry-After": "7"}
