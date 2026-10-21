"""`UsageGuard` at its boundary: in-memory store, fixed clock, no DB."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from services.usage import (
    ActiveCounts,
    ConcurrencyLimitedError,
    DailyLimit,
    LimitExceededError,
    UsageKind,
    UsageUnavailableError,
)
from tests.services.memory_usage_store import NOW, MemoryUsageStore, guard, limits

GEMINI = UsageKind.GEMINI_REQUEST


async def _call(usage, business_id, *, tokens=(0, 0)):
    async with usage.metered(GEMINI, business_id=business_id) as meter:
        meter.record(tokens_in=tokens[0], tokens_out=tokens[1])


async def test_counts_each_request_globally_and_for_the_business():
    store = MemoryUsageStore()
    business = uuid.uuid4()
    await _call(guard(store), business)
    assert store.count(GEMINI, business) == 1
    assert store.count(GEMINI) == 1


async def test_business_limit_blocks_only_that_business_and_never_runs_the_body():
    store = MemoryUsageStore()
    usage = guard(store, limits(gemini_request=DailyLimit(per_day=100, per_business_per_day=2)))
    alpha, beta = uuid.uuid4(), uuid.uuid4()
    await _call(usage, alpha)
    await _call(usage, alpha)

    ran = False
    with pytest.raises(LimitExceededError) as refused:
        async with usage.metered(GEMINI, business_id=alpha):
            ran = True
    assert not ran
    assert refused.value.scope == "business"
    await _call(usage, beta)  # another tenant is unaffected
    assert store.count(GEMINI, alpha) == 2 and store.count(GEMINI, beta) == 1


async def test_global_limit_blocks_every_business():
    store = MemoryUsageStore()
    usage = guard(store, limits(gemini_request=DailyLimit(per_day=2, per_business_per_day=50)))
    await _call(usage, uuid.uuid4())
    await _call(usage, uuid.uuid4())
    for business in (uuid.uuid4(), uuid.uuid4()):
        with pytest.raises(LimitExceededError) as refused:
            await _call(usage, business)
        assert refused.value.scope == "global"


@pytest.mark.parametrize(
    ("limit", "scope"),
    [
        (DailyLimit(per_day=0, per_business_per_day=5), "global"),
        (DailyLimit(per_day=5, per_business_per_day=0), "business"),
    ],
)
async def test_a_zero_limit_refuses_without_touching_the_store(limit, scope):
    store = MemoryUsageStore()
    with pytest.raises(LimitExceededError) as refused:
        await _call(guard(store, limits(gemini_request=limit)), uuid.uuid4())
    assert refused.value.scope == scope
    assert store.events == []


async def test_the_window_is_the_utc_day():
    store = MemoryUsageStore()
    clock = [NOW]
    usage = guard(
        store,
        limits(gemini_request=DailyLimit(per_day=1, per_business_per_day=1)),
        clock=lambda: clock[0],
    )
    business = uuid.uuid4()
    await _call(usage, business)
    with pytest.raises(LimitExceededError):
        await _call(usage, business)

    clock[0] = datetime(2026, 10, 22, 0, 0, tzinfo=UTC)  # UTC midnight: a fresh window
    await _call(usage, business)
    window = usage.window()
    assert window.starts_at == datetime(2026, 10, 22, tzinfo=UTC)
    assert window.resets_at - window.starts_at == timedelta(days=1)


async def test_a_failed_call_stays_counted_and_records_failed_request():
    store = MemoryUsageStore()
    business = uuid.uuid4()
    with pytest.raises(RuntimeError):
        async with guard(store).metered(GEMINI, business_id=business):
            raise RuntimeError("provider down")
    assert store.count(GEMINI, business) == 1
    assert store.count(UsageKind.FAILED_REQUEST, business) == 1


async def test_a_cancelled_call_is_also_recorded_as_failed():
    store = MemoryUsageStore()
    business = uuid.uuid4()

    async def slow():
        async with guard(store).metered(GEMINI, business_id=business):
            await asyncio.sleep(10)

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.01):
            await slow()
    assert store.count(UsageKind.FAILED_REQUEST, business) == 1


async def test_tokens_and_estimated_cost_are_recorded_from_settings_rates():
    store = MemoryUsageStore()
    business = uuid.uuid4()
    await _call(guard(store), business, tokens=(1_000_000, 100_000))
    assert store.count(UsageKind.GEMINI_TOKENS, business) == 1_100_000
    summary = await guard(store).summary(business)
    assert summary.estimated_cost_usd == Decimal("0.30") + Decimal("0.25")
    assert summary.gemini_tokens == 1_100_000


async def test_bookkeeping_failure_after_the_call_never_fails_the_caller():
    store = MemoryUsageStore()
    store.fail_record = True
    await _call(guard(store), uuid.uuid4(), tokens=(10, 10))  # does not raise


async def test_unlimited_kinds_are_metered_but_never_blocked():
    store = MemoryUsageStore()
    usage = guard(store)
    for _ in range(3):
        async with usage.metered(UsageKind.BILLING_CALL, business_id=None):
            pass
    assert store.count(UsageKind.BILLING_CALL) == 3


async def test_summary_reports_the_business_and_only_a_flag_for_global():
    store = MemoryUsageStore()
    usage = guard(store, limits(gemini_request=DailyLimit(per_day=2, per_business_per_day=5)))
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    await _call(usage, mine)
    await _call(usage, theirs)
    with pytest.raises(LimitExceededError):
        await _call(usage, mine)

    # Refused at the global cap: neither counted nor a failed request.
    summary = await usage.summary(mine)
    gemini = next(m for m in summary.meters if m.kind == "gemini_request")
    assert (gemini.used, gemini.limit, gemini.global_limit_reached) == (1, 5, True)
    assert summary.failed_requests == 0
    assert summary.window.timezone == "UTC"


@pytest.mark.parametrize(
    ("active", "refused"),
    [
        (ActiveCounts(all_businesses=99, business=9), False),
        (ActiveCounts(all_businesses=100, business=0), True),
        (ActiveCounts(all_businesses=5, business=10), True),
    ],
)
def test_admission_checks_global_then_business_concurrency(active, refused):
    usage = guard()
    if refused:
        with pytest.raises(ConcurrencyLimitedError):
            usage.check_admission(active)
    else:
        usage.check_admission(active)


async def test_a_store_outage_fails_closed_for_capped_kinds():
    store = MemoryUsageStore()
    store.fail_reserve = True
    ran = False
    with pytest.raises(UsageUnavailableError):
        async with guard(store).metered(GEMINI, business_id=uuid.uuid4()):
            ran = True
    assert not ran


async def test_a_store_outage_fails_open_for_metered_only_kinds():
    store = MemoryUsageStore()
    store.fail_reserve = True
    ran = False
    async with guard(store).metered(UsageKind.BILLING_CALL, business_id=None):
        ran = True
    assert ran


async def test_tokens_spent_by_a_failed_call_are_still_recorded():
    store = MemoryUsageStore()
    business = uuid.uuid4()
    with pytest.raises(RuntimeError):
        async with guard(store).metered(GEMINI, business_id=business) as meter:
            meter.record(tokens_in=500, tokens_out=2048)
            raise RuntimeError("truncated")
    assert store.count(UsageKind.GEMINI_TOKENS, business) == 2548
    assert store.count(UsageKind.FAILED_REQUEST, business) == 1
