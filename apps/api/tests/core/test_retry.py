import pytest

from core.retry import MAX_ATTEMPTS_CEILING, RetryPolicy, retry_bounded


class Transient(Exception):
    pass


class Permanent(Exception):
    pass


def _flaky(*outcomes):
    """An async callable that raises or returns each outcome in turn, counting calls."""
    calls = []

    async def fn():
        calls.append(1)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return fn, calls


def _is_transient(exc: Exception) -> bool:
    return isinstance(exc, Transient)


async def test_returns_after_a_transient_failure_within_budget():
    fn, calls = _flaky(Transient(), "ok")
    assert await retry_bounded(fn, RetryPolicy(attempts=2), _is_transient) == "ok"
    assert len(calls) == 2


async def test_gives_up_after_the_attempt_budget_and_reraises_the_last_error():
    fn, calls = _flaky(Transient("1"), Transient("2"), "never reached")
    with pytest.raises(Transient, match="2"):
        await retry_bounded(fn, RetryPolicy(attempts=2), _is_transient)
    assert len(calls) == 2


async def test_errors_outside_retry_if_fail_immediately():
    fn, calls = _flaky(Permanent(), "never reached")
    with pytest.raises(Permanent):
        await retry_bounded(fn, RetryPolicy(attempts=3), _is_transient)
    assert len(calls) == 1


async def test_backoff_doubles_between_attempts(monkeypatch):
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr("core.retry.asyncio.sleep", fake_sleep)
    fn, _ = _flaky(Transient(), Transient(), "ok")
    await retry_bounded(fn, RetryPolicy(attempts=3, backoff_seconds=0.5), _is_transient)
    assert delays == [0.5, 1.0]


@pytest.mark.parametrize("attempts", [0, MAX_ATTEMPTS_CEILING + 1])
def test_policies_outside_the_ceiling_are_rejected(attempts):
    with pytest.raises(ValueError):
        RetryPolicy(attempts=attempts)
