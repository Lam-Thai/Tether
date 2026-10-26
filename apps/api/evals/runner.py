"""`EvalRunner.run(suite) -> EvalReport`: plays a suite against a model and scores it.

One call hides everything a run needs:

- Fixture setup. The runner opens a sandbox (`SandboxFactory`) for the whole
  run: in real runs a throwaway synthetic salon tenant in Postgres, seeded from
  `seeds/demo_salon.py` and deleted afterwards (`evals/sandbox.py`). Each case
  gets a new conversation in it, so cases never see each other's history.
- Budgeting. Every model call still goes through the engine's `UsageGuard`;
  the sandbox caps the run's tenant at `max_requests` Gemini requests. When
  the cap (or a global limit) is reached the engine answers with its quota
  fallback, and the runner stops cleanly: cases not yet played are reported
  `not_run` and the metrics cover what was scored.
- Pacing. Model calls are spaced to `requests_per_minute` across the whole
  run (free-tier friendly); the engine's per-turn deadline gets an allowance
  for that waiting, so pacing never turns into a timeout.
- Rate-limit backoff. A case whose turn fell back because the provider kept
  answering 429 is replayed after an exponential backoff, up to
  `max_rate_limit_retries` times (never sooner than the provider's own retry
  delay); after that the run stops (`rate_limited`). A 429 whose retry delay
  is over `MAX_RATE_LIMIT_WAIT_SECONDS` (a daily quota) stops the run at once
  (`quota_exhausted`). An outage that isn't a 429 is scored as what the
  customer got.
- Errors. A case that raises is recorded as `error` (type name only) and the
  run goes on; `MAX_CONSECUTIVE_ERRORS` in a row stop it (`errors`). With
  concurrency above 1 "in a row" means in completion order, so it's a
  heuristic for "the environment is broken", not an exact count.
- Scoring and the report (`evals/scorers.py`, `evals/report.py`).

Reproducible inputs: the same cases, in file order, against the same seed
data and a pinned business clock. The model itself is not deterministic, so
compare rates across runs, not individual replies.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal

from evals.cases import EvalCase, Suite
from evals.playback import Playback, Recorder, Sandbox, play, play_summary
from evals.report import CaseResult, EvalReport, StopReason, build_metrics
from evals.scorers import SCORERS, Score, Scorer
from integrations.llm.port import LLMClient, ModelReply, ModelRequest
from services.conversation import MAX_TOOL_ROUNDS

# At most this many model calls in one turn: the tool rounds, the answer, one retry.
MAX_CALLS_PER_TURN = MAX_TOOL_ROUNDS + 2
MAX_CONSECUTIVE_ERRORS = 3
# A 429 asking to wait longer than this is a used-up quota (Gemini's free tier is per
# day): stop at once and report it rather than back off.
MAX_RATE_LIMIT_WAIT_SECONDS = 300.0

Tier = Literal["free", "paid"]


@dataclass(frozen=True)
class RunLimits:
    max_requests: int
    requests_per_minute: float
    concurrency: int
    rate_limit_backoff_seconds: float
    max_rate_limit_retries: int

    def __post_init__(self) -> None:
        if self.max_requests < 1 or self.requests_per_minute <= 0 or self.concurrency < 1:
            raise ValueError("max_requests, requests_per_minute and concurrency must be positive.")
        if self.rate_limit_backoff_seconds < 0 or not 0 <= self.max_rate_limit_retries <= 5:
            raise ValueError("Backoff must be >= 0 and at most 5 rate-limit retries.")

    @property
    def interval_seconds(self) -> float:
        return 60.0 / self.requests_per_minute


# Defaults per Gemini API tier; `EVAL_*` settings and CLI flags override each value.
# Free: Gemini 2.5 Flash's free tier allows about 20 requests a day per project and a
# few a minute (measured October 2026), so the budget fits one day and the pace stays
# under the minute limit; that covers the `smoke` cases (`--tag smoke`), not the whole
# suite. Paid: the whole suite, several cases at once, short backoffs.
TIER_LIMITS: Mapping[Tier, RunLimits] = {
    "free": RunLimits(
        max_requests=20,
        requests_per_minute=4,
        concurrency=1,
        rate_limit_backoff_seconds=60,
        max_rate_limit_retries=2,
    ),
    "paid": RunLimits(
        max_requests=600,
        requests_per_minute=240,
        concurrency=4,
        rate_limit_backoff_seconds=5,
        max_rate_limit_retries=4,
    ),
}


def resolve_limits(
    tier: Tier,
    *,
    max_requests: int | None = None,
    requests_per_minute: float | None = None,
    concurrency: int | None = None,
) -> RunLimits:
    """The tier's defaults with any given value overriding its counterpart."""
    overrides = {
        "max_requests": max_requests,
        "requests_per_minute": requests_per_minute,
        "concurrency": concurrency,
    }
    return replace(TIER_LIMITS[tier], **{k: v for k, v in overrides.items() if v is not None})


@dataclass(frozen=True)
class SandboxSpec:
    """What the runner asks of a sandbox for one run."""

    max_requests: int
    concurrency: int
    # Added to the engine's per-turn deadline: time a turn may spend waiting for pacing.
    pacing_allowance_seconds: float


SandboxFactory = Callable[[SandboxSpec], AbstractAsyncContextManager[Sandbox]]


class _Pacer:
    """Spaces model calls `interval` seconds apart across the whole run, and counts them."""

    def __init__(
        self,
        inner: LLMClient,
        interval: float,
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
    ) -> None:
        self._inner = inner
        self._interval = interval
        self._clock = clock
        self._sleep = sleep
        self._next_at: float | None = None
        self._lock = asyncio.Lock()
        self.calls = 0
        self.tokens_in = 0
        self.tokens_out = 0

    async def generate(self, request: ModelRequest) -> ModelReply:
        async with self._lock:
            now = self._clock()
            start = now if self._next_at is None else max(now, self._next_at)
            if start > now:
                await self._sleep(start - now)
            self._next_at = start + self._interval
        self.calls += 1
        reply = await self._inner.generate(request)
        self.tokens_in += reply.tokens_in
        self.tokens_out += reply.tokens_out
        return reply


class EvalRunner:
    def __init__(
        self,
        sandbox: SandboxFactory,
        llm: LLMClient,
        limits: RunLimits,
        *,
        model: str,
        tier: Tier,
        scorers: Mapping[str, Scorer] = SCORERS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sandbox = sandbox
        self._llm = llm
        self._limits = limits
        self._model = model
        self._tier: Tier = tier
        self._scorers = scorers
        self._clock = clock
        self._sleep = sleep
        self._now = now

    async def run(self, suite: Suite) -> EvalReport:
        started_at = self._now()
        limits = self._limits
        pacer = _Pacer(self._llm, limits.interval_seconds, self._clock, self._sleep)
        spec = SandboxSpec(
            max_requests=limits.max_requests,
            concurrency=limits.concurrency,
            # Each call of a turn may queue behind one call of every other running case.
            pacing_allowance_seconds=(
                MAX_CALLS_PER_TURN * limits.concurrency * limits.interval_seconds
            ),
        )
        results: list[CaseResult] = [CaseResult(case, "not_run") for case in suite.cases]
        state = _RunState()
        gate = asyncio.Semaphore(limits.concurrency)

        async with self._sandbox(spec) as sandbox:

            async def run_one(index: int, case: EvalCase) -> None:
                async with gate:
                    if state.stopped is None:
                        results[index] = await self._run_case(sandbox, pacer, case, state)

            await asyncio.gather(*(run_one(i, case) for i, case in enumerate(suite.cases)))

        final = tuple(results)
        return EvalReport(
            suite=suite.name,
            model=self._model,
            tier=self._tier,
            max_requests=limits.max_requests,
            started_at=started_at,
            finished_at=self._now(),
            stopped=state.stopped,
            retry_after_seconds=state.retry_after_seconds,
            model_calls=pacer.calls,
            tokens_in=pacer.tokens_in,
            tokens_out=pacer.tokens_out,
            metrics=build_metrics(suite, final),
            cases=final,
        )

    async def _run_case(
        self, sandbox: Sandbox, pacer: _Pacer, case: EvalCase, state: "_RunState"
    ) -> CaseResult:
        playback: Playback | None = None
        for attempt in range(self._limits.max_rate_limit_retries + 1):
            recorder = Recorder(pacer)
            try:
                if case.stored is not None:
                    playback = await play_summary(sandbox, recorder, case.stored)
                else:
                    async with sandbox.case(recorder, case.calendar) as env:
                        playback = await play(env, recorder, case.turns)
            except Exception as exc:  # recorded by type only, see `CaseResult.error`
                state.errors_in_a_row += 1
                if state.errors_in_a_row >= MAX_CONSECUTIVE_ERRORS:
                    state.stop("errors")
                return CaseResult(case, "error", error=type(exc).__name__)
            state.errors_in_a_row = 0

            if playback.usage_limited:
                state.stop("budget_exhausted")
                return CaseResult(case, "not_run", playback=playback)
            if not playback.rate_limited_out:
                return CaseResult(case, "scored", playback, self._score(case, playback))
            delay = playback.retry_after_seconds
            if delay is not None and delay > MAX_RATE_LIMIT_WAIT_SECONDS:
                state.retry_after_seconds = delay
                state.stop("quota_exhausted")
                return CaseResult(case, "not_run", playback=playback)
            if attempt == self._limits.max_rate_limit_retries:
                break
            backoff = self._limits.rate_limit_backoff_seconds * 2**attempt
            await self._sleep(max(backoff, delay or 0.0))
            if state.stopped:  # another case ended the run while this one backed off
                return CaseResult(case, "not_run", playback=playback)
        state.stop("rate_limited")
        return CaseResult(case, "not_run", playback=playback)

    def _score(self, case: EvalCase, playback: Playback) -> tuple[Score, ...]:
        return tuple(
            self._scorers[name].score(case, playback.turns, playback.operations)
            for name in case.scorers
        )


@dataclass
class _RunState:
    stopped: StopReason | None = None
    errors_in_a_row: int = 0
    retry_after_seconds: float | None = None

    def stop(self, reason: StopReason) -> None:
        if self.stopped is None:
            self.stopped = reason
