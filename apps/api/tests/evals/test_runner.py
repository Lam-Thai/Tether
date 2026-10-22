"""`EvalRunner.run(suite) -> EvalReport` at its boundary: a scripted fake model over the
in-memory demo salon. No provider calls, no database."""

import json
from dataclasses import replace

import pytest

from evals.cases import EvalCase, Metric, Suite
from evals.report import summary, to_markdown, write
from evals.runner import MAX_CALLS_PER_TURN, TIER_LIMITS, EvalRunner, RunLimits, resolve_limits
from integrations.llm.fake import FakeLLMClient, agent_turn
from integrations.llm.port import LLMUnavailableError
from seeds.demo_salon import WALK_IN_QUESTION
from tests.evals.memory_sandbox import MemorySandbox

LIMITS = RunLimits(
    max_requests=100,
    requests_per_minute=60,
    concurrency=1,
    rate_limit_backoff_seconds=30,
    max_rate_limit_retries=2,
)
METRICS = (
    Metric(scorer="intent_match", label="intent accuracy", target=0.9),
    Metric(scorer="grounded_in_faq", label="faq grounding", target=0.95),
    Metric(scorer="tools_used", label="expected tool use"),
)


class FakeTime:
    """A monotonic clock that only moves when the runner sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def walk_in_case(case_id: str = "walk-in") -> EvalCase:
    return EvalCase(
        id=case_id,
        turns=("can I just walk in?",),
        expected_intent="FAQ",
        expected_tools=("search_faq",),
        must_contain=("3",),
        scorers=("intent_match", "grounded_in_faq", "tools_used"),
    )


def hours_case(case_id: str = "hours") -> EvalCase:
    return EvalCase(
        id=case_id,
        turns=("When are you open Saturday?",),
        expected_intent="BUSINESS_HOURS",
        scorers=("intent_match",),
    )


def suite(*cases: EvalCase) -> Suite:
    return Suite(name="test", metrics=METRICS, cases=cases)


def search_walk_ins() -> dict:
    return agent_turn(
        "Let me check.",
        intent="FAQ",
        tool_calls=[{"name": "search_faq", "arguments": {"query": WALK_IN_QUESTION}}],
    )


def walk_in_answer(sandbox: MemorySandbox) -> dict:
    return agent_turn(
        "Yes, we take walk-ins before 3 PM, Monday to Saturday.",
        intent="FAQ",
        sources=[str(sandbox.faq_ids[WALK_IN_QUESTION])],
    )


def hours_answer() -> dict:
    return agent_turn(
        "On Saturday we're open 10 AM to 4 PM.", intent="BUSINESS_HOURS", sources=["business_hours"]
    )


def runner(
    sandbox: MemorySandbox, llm: FakeLLMClient, limits: RunLimits = LIMITS, time=None
) -> EvalRunner:
    time = time or FakeTime()
    return EvalRunner(
        sandbox, llm, limits, model="fake-model", tier="free", clock=time.clock, sleep=time.sleep
    )


async def test_scores_every_case_and_reports_metrics_against_targets():
    sandbox, llm = MemorySandbox(), FakeLLMClient()
    llm.queue(search_walk_ins(), walk_in_answer(sandbox), hours_answer())

    report = await runner(sandbox, llm).run(suite(walk_in_case(), hours_case()))

    assert [c.status for c in report.cases] == ["scored", "scored"]
    assert all(c.passed for c in report.cases), [c.scores for c in report.cases]
    by_label = {m.label: (m.passed, m.total, m.met) for m in report.metrics}
    assert by_label == {
        "intent accuracy": (2, 2, True),
        "faq grounding": (1, 1, True),
        "expected tool use": (1, 1, None),  # no target: informational
    }
    assert report.complete and report.stopped is None and report.exit_code() == 0
    assert report.model_calls == 3 == len(llm.requests)
    assert report.tokens_in == 300 and report.tokens_out == 60
    walk_in = report.cases[0].playback
    assert walk_in is not None
    assert [op.tool_name for op in walk_in.operations] == ["search_faq"]
    assert sandbox.closed  # the sandbox is torn down after the run


async def test_each_case_is_a_new_conversation_with_the_turns_in_order():
    sandbox, llm = MemorySandbox(), FakeLLMClient()
    convo = EvalCase(
        id="convo",
        turns=("hi there", "When are you open Saturday?"),
        expected_intent="BUSINESS_HOURS",
        scorers=("intent_match",),
    )
    llm.queue(agent_turn("Hi! How can I help?", intent="UNKNOWN"), hours_answer(), hours_answer())

    report = await runner(sandbox, llm).run(suite(convo, hours_case()))

    played = report.cases[0].playback
    assert played is not None
    assert [t.customer for t in played.turns] == ["hi there", "When are you open Saturday?"]
    assert report.cases[0].passed
    # The second case started fresh: one customer message in its first request.
    assert [m.role for m in llm.requests[2].messages] == ["model", "user"]
    assert all(i.status == "ended" for i in sandbox.store.interactions.values())
    # Committed like the chat API: after every turn and after ending (3 + 2).
    assert sandbox.store.calls.count("commit") == 5


async def test_intent_is_the_models_own_even_when_the_guard_replaces_the_reply():
    sandbox, llm = MemorySandbox(), FakeLLMClient()
    # An FAQ answer citing nothing: the grounding guard swaps in the no-info fallback.
    llm.queue(agent_turn("Sure, come by any time!", intent="FAQ"))

    report = await runner(sandbox, llm).run(suite(walk_in_case()))

    result = report.cases[0]
    turn = result.playback.turns[-1] if result.playback else None
    assert turn is not None and turn.intent == "UNKNOWN" and turn.model_intent == "FAQ"
    assert turn.fallback == "no_info"
    scores = {s.scorer: s for s in result.scores}
    assert scores["intent_match"].passed
    assert not scores["grounded_in_faq"].passed
    assert "no information" in scores["grounded_in_faq"].detail
    assert not scores["tools_used"].passed
    assert report.exit_code() == 1  # complete, but a target was missed


async def test_stops_cleanly_with_partial_results_when_the_budget_runs_out():
    sandbox, llm = MemorySandbox(), FakeLLMClient()
    # Budget 3: the first case uses 2 requests, the second is refused on its 2nd.
    llm.queue(search_walk_ins(), walk_in_answer(sandbox), search_walk_ins())

    report = await runner(sandbox, llm, replace(LIMITS, max_requests=3)).run(
        suite(walk_in_case("first"), walk_in_case("second"), hours_case())
    )

    assert [c.status for c in report.cases] == ["scored", "not_run", "not_run"]
    assert report.stopped == "budget_exhausted"
    assert not report.complete and report.exit_code() == 2
    assert len(llm.requests) == 3 == report.model_calls  # never past the budget
    intent = next(m for m in report.metrics if m.scorer == "intent_match")
    assert (intent.passed, intent.total) == (1, 1)  # only scored cases count
    assert "PARTIAL" in summary(report)


async def test_a_rate_limited_case_is_replayed_after_a_backoff():
    sandbox, llm, time = MemorySandbox(), FakeLLMClient(), FakeTime()
    quota = LLMUnavailableError("429", retryable=True, rate_limited=True)
    llm.queue(quota, quota, hours_answer())  # both engine attempts 429, then the replay works

    report = await runner(sandbox, llm, time=time).run(suite(hours_case()))

    assert report.cases[0].status == "scored" and report.cases[0].passed
    assert 30 in time.sleeps  # the backoff, before the replay
    assert report.complete


async def test_stops_when_the_provider_keeps_rate_limiting():
    sandbox, llm, time = MemorySandbox(), FakeLLMClient(), FakeTime()
    quota = LLMUnavailableError("429", retryable=True, rate_limited=True)
    llm.queue(*[quota] * 6)  # 2 engine attempts x (1 try + 2 replays)

    report = await runner(sandbox, llm, time=time).run(suite(hours_case("a"), hours_case("b")))

    assert [c.status for c in report.cases] == ["not_run", "not_run"]
    assert report.stopped == "rate_limited" and report.exit_code() == 2
    backoffs = [s for s in time.sleeps if s >= 30]
    assert backoffs == [30, 60]  # exponential, then give up
    assert len(llm.requests) == 6


async def test_a_used_up_daily_quota_stops_the_run_at_once():
    sandbox, llm, time = MemorySandbox(), FakeLLMClient(), FakeTime()
    daily = LLMUnavailableError(
        "429", retryable=True, rate_limited=True, retry_after_seconds=39_801
    )
    llm.queue(daily, daily)

    report = await runner(sandbox, llm, time=time).run(suite(hours_case("a"), hours_case("b")))

    assert report.stopped == "quota_exhausted" and report.retry_after_seconds == 39_801
    assert not [s for s in time.sleeps if s >= 30]  # no pointless backoff
    assert len(llm.requests) == 2 and report.exit_code() == 2
    assert "retry in 11.1h" in summary(report)


async def test_a_backoff_never_undercuts_the_providers_retry_delay():
    sandbox, llm, time = MemorySandbox(), FakeLLMClient(), FakeTime()
    minute = LLMUnavailableError("429", retryable=True, rate_limited=True, retry_after_seconds=45)
    llm.queue(minute, minute, hours_answer())

    report = await runner(sandbox, llm, time=time).run(suite(hours_case()))

    assert report.cases[0].passed and 45 in time.sleeps  # max(30, 45)


async def test_an_outage_that_is_not_a_rate_limit_is_scored_not_replayed():
    sandbox, llm, time = MemorySandbox(), FakeLLMClient(), FakeTime()
    down = LLMUnavailableError("503", retryable=True)
    llm.queue(down, down)

    report = await runner(sandbox, llm, time=time).run(suite(hours_case()))

    assert report.cases[0].status == "scored" and not report.cases[0].passed
    assert report.cases[0].playback and report.cases[0].playback.turns[0].fallback == "provider"
    assert not [s for s in time.sleeps if s >= 30]


async def test_model_calls_are_paced_and_the_turn_deadline_allows_for_it():
    sandbox, llm, time = MemorySandbox(), FakeLLMClient(), FakeTime()
    llm.queue(search_walk_ins(), walk_in_answer(sandbox), hours_answer())

    await runner(sandbox, llm, time=time).run(suite(walk_in_case(), hours_case()))

    assert time.sleeps == [1.0, 1.0]  # 60/min: a second between the 3 calls
    assert sandbox.specs[0].pacing_allowance_seconds == MAX_CALLS_PER_TURN * 1.0


async def test_the_pacing_allowance_covers_queueing_behind_other_cases():
    sandbox, llm = MemorySandbox(), FakeLLMClient()
    llm.queue(hours_answer())

    await runner(sandbox, llm, replace(LIMITS, concurrency=3)).run(suite(hours_case()))

    assert sandbox.specs[0].pacing_allowance_seconds == MAX_CALLS_PER_TURN * 3 * 1.0
    assert sandbox.specs[0].max_requests == LIMITS.max_requests


async def test_a_case_that_errors_is_recorded_and_the_run_goes_on():
    sandbox, llm = MemorySandbox(fail_cases=1), FakeLLMClient()
    llm.queue(hours_answer())

    report = await runner(sandbox, llm).run(suite(hours_case("broken"), hours_case("fine")))

    broken, fine = report.cases
    assert broken.status == "error" and broken.error == "RuntimeError"
    assert fine.status == "scored" and fine.passed
    assert report.stopped is None and report.exit_code() == 2  # partial: one case errored


async def test_repeated_errors_stop_the_run():
    sandbox, llm = MemorySandbox(fail_cases=10), FakeLLMClient()

    report = await runner(sandbox, llm).run(suite(*(hours_case(f"c{i}") for i in range(5))))

    assert [c.status for c in report.cases] == ["error"] * 3 + ["not_run"] * 2
    assert report.stopped == "errors"


async def test_cases_can_run_concurrently():
    sandbox, llm = MemorySandbox(), FakeLLMClient()
    llm.queue(*[hours_answer()] * 4)

    report = await runner(sandbox, llm, replace(LIMITS, concurrency=4)).run(
        suite(*(hours_case(f"c{i}") for i in range(4)))
    )

    assert all(c.passed for c in report.cases) and report.complete


async def test_report_shows_failures_with_their_transcript(tmp_path):
    sandbox, llm = MemorySandbox(), FakeLLMClient()
    llm.queue(agent_turn("Sure, come by any time!", intent="FAQ"), hours_answer())

    report = await runner(sandbox, llm).run(suite(walk_in_case(), hours_case()))
    json_path, md_path = write(report, tmp_path)

    markdown = md_path.read_text(encoding="utf-8")
    assert markdown == to_markdown(report)
    assert "### `walk-in`" in markdown and "### `hours`" not in markdown  # failures only
    assert "**Customer:** can I just walk in?" in markdown
    assert "Sorry, I don't have that information." in markdown  # what the customer got
    assert "model FAQ" in markdown  # what the model proposed
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["complete"] is True and data["suite"] == "test"
    failing = next(c for c in data["cases"] if c["id"] == "walk-in")
    assert failing["passed"] is False and failing["transcript"][0]["model_intent"] == "FAQ"
    assert json_path.stem == md_path.stem == report.started_at.strftime("%Y-%m-%dT%H%M%SZ")


def test_paid_tier_has_fewer_limits_than_free():
    free, paid = TIER_LIMITS["free"], TIER_LIMITS["paid"]
    assert paid.max_requests > free.max_requests
    assert paid.requests_per_minute > free.requests_per_minute
    assert paid.concurrency > free.concurrency
    assert paid.rate_limit_backoff_seconds < free.rate_limit_backoff_seconds


def test_settings_or_flags_override_one_tier_value_at_a_time():
    limits = resolve_limits("free", max_requests=50, concurrency=None)
    assert limits == replace(TIER_LIMITS["free"], max_requests=50)
    with pytest.raises(ValueError):
        resolve_limits("free", requests_per_minute=0)
