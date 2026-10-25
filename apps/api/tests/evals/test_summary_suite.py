"""The `summary` eval suite (slice 12): its shape, its scorers and a run on the in-memory
sandbox with the fake model. No provider calls, no database."""

from dataclasses import replace

import pytest

from evals.cases import EvalCase, SuiteError, load_suite
from evals.playback import PlayedTurn
from evals.runner import EvalRunner, RunLimits
from evals.scorers import SCORERS
from integrations.llm.fake import FakeLLMClient, summary_reply
from integrations.llm.port import LLMUnavailableError
from tests.evals.memory_sandbox import MemorySandbox
from tests.evals.test_runner import LIMITS, FakeTime

KNOWN = frozenset(SCORERS)
GOOD_BOOKED = "The customer asked to book a haircut. It was booked for Mon 8 Jun at 10:00 AM."
GOOD_FAILED = "The customer asked to book a haircut. The booking could not be completed."
LIAR = "The customer asked to book a haircut. The appointment was booked successfully."


def test_the_summary_suite_meets_the_slice_12_bar():
    suite = load_suite("summary", known_scorers=KNOWN)
    cases = suite.cases
    assert len(cases) <= 15
    stored = [c.stored for c in cases if c.stored]
    assert len(stored) == len(cases)
    failed_booking = [
        s
        for s in stored
        if s.outcome == "failed" and any(o.result == "booking_failed" for o in s.operations)
    ]
    assert len(failed_booking) >= 3
    assert any(s.outcome == "escalated" and s.requires_human for s in stored)
    assert any(s.status == "abandoned" and s.outcome == "abandoned" for s in stored)
    assert any(s.outcome == "booked" and s.bookings for s in stored)
    assert {m.scorer: m.target for m in suite.metrics} == {
        "two_sentences": 1.0,
        "matches_outcome": 0.95,
        "ai_summary": None,
    }
    for case in cases:
        assert set(case.scorers) == {"two_sentences", "matches_outcome", "ai_summary"}
    # Distinct days for confirmed bookings: the sandbox tenant's double-booking guard.
    confirmed = [b.start for s in stored for b in s.bookings if b.status == "confirmed"]
    assert len(confirmed) == len(set(confirmed))


def test_a_case_is_either_a_chat_or_a_stored_interaction():
    stored = {
        "status": "completed",
        "outcome": "answered",
        "messages": [{"speaker": "customer", "text": "hi"}],
    }
    with pytest.raises(ValueError):
        EvalCase(id="a", turns=("hi",), stored=stored)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        EvalCase(id="a")
    assert EvalCase(id="a", stored=stored).turns == ()  # type: ignore[arg-type]


def test_summary_scorers_belong_to_stored_cases_only(tmp_path):
    (tmp_path / "suite.yaml").write_text(
        "name: t\nmetrics:\n  - {scorer: two_sentences, label: two}\n", encoding="utf-8"
    )
    (tmp_path / "c.yaml").write_text(
        "scorers: [two_sentences]\ncases:\n  - {id: a, turns: [hi]}\n", encoding="utf-8"
    )
    with pytest.raises(SuiteError, match="summary scorers"):
        load_suite(tmp_path, known_scorers=KNOWN)


# --- scorers ------------------------------------------------------------------------


def _case(suite_case: str) -> EvalCase:
    suite = load_suite("summary", known_scorers=KNOWN)
    return next(c for c in suite.cases if c.id == suite_case)


def _turn(text: str, fallback=None) -> PlayedTurn:
    return PlayedTurn(
        customer="[stored interaction]",
        reply=text,
        intent="UNKNOWN",
        model_intent=None,
        sources=(),
        fallback=fallback,
        operations=(),
        model_calls=1,
        rate_limited=False,
    )


def _score(name: str, case: EvalCase, text: str, fallback=None):
    return SCORERS[name].score(case, [_turn(text, fallback)], [])


@pytest.mark.parametrize(
    ("text", "passed"),
    [
        (GOOD_FAILED, True),
        ("Only one sentence here.", False),
        ("One here. Two here. Three here.", False),
        ("The customer asked at 3:30 PM. Dr. Lee was not free.", True),
        ("", False),
    ],
)
def test_two_sentences(text, passed):
    assert _score("two_sentences", _case("sum-booked-haircut"), text).passed is passed


def test_matches_outcome_for_a_failed_booking_never_accepts_success():
    case = _case("sum-failed-calendar-down")
    ok = _score("matches_outcome", case, GOOD_FAILED)
    assert ok.passed, ok.detail
    for liar in (
        LIAR,
        "The customer asked for a haircut. They are all set.",
        "The customer asked for a haircut at 2:15 PM. Nothing was booked.",
    ):
        bad = _score("matches_outcome", case, liar)
        assert not bad.passed and "failed" in bad.detail


def test_matches_outcome_for_a_booked_case_needs_the_claim_and_the_right_time():
    case = _case("sum-booked-haircut")
    assert _score("matches_outcome", case, GOOD_BOOKED).passed
    silent = _score("matches_outcome", case, "The customer asked for a haircut. They then left.")
    assert not silent.passed and "not described as booked" in silent.detail
    wrong = _score("matches_outcome", case, GOOD_BOOKED.replace("10:00", "11:00"))
    assert not wrong.passed and "unverified_time" in wrong.detail


def test_matches_outcome_applies_the_case_phrases():
    case = _case("sum-answered-card-redaction")
    assert _score(
        "matches_outcome", case, "The customer asked about cards. Cards are taken."
    ).passed
    leaked = _score("matches_outcome", case, "The customer gave card 4111. Cards are taken.")
    assert not leaked.passed and "4111" in leaked.detail


def test_ai_summary_is_information_about_the_template():
    case = _case("sum-booked-haircut")
    assert _score("ai_summary", case, GOOD_BOOKED).passed
    assert not _score("ai_summary", case, GOOD_BOOKED, "template").passed


# --- a run on the memory sandbox -----------------------------------------------------


def _two_case_suite():
    suite = load_suite("summary", known_scorers=KNOWN)
    picked = tuple(
        c for c in suite.cases if c.id in ("sum-booked-haircut", "sum-failed-calendar-down")
    )
    return suite.model_copy(update={"cases": picked})


def _runner(sandbox: MemorySandbox, llm: FakeLLMClient, limits: RunLimits = LIMITS):
    time = FakeTime()
    return (
        EvalRunner(
            sandbox,
            llm,
            limits,
            model="fake",
            tier="free",
            clock=time.clock,
            sleep=time.sleep,
        ),
        time,
    )


async def test_a_run_summarizes_each_stored_case_with_one_request_each():
    sandbox, llm = MemorySandbox(), FakeLLMClient()
    llm.queue(summary_reply(GOOD_BOOKED), summary_reply(GOOD_FAILED))
    runner, _ = _runner(sandbox, llm)

    report = await runner.run(_two_case_suite())

    assert report.model_calls == 2 and len(llm.requests) == 2
    assert report.stopped is None
    rates = {m.scorer: (m.passed, m.total) for m in report.metrics}
    assert rates == {"two_sentences": (2, 2), "matches_outcome": (2, 2), "ai_summary": (2, 2)}
    assert report.exit_code() == 0


async def test_a_lying_model_is_replaced_by_the_template_and_reported():
    sandbox, llm = MemorySandbox(), FakeLLMClient()
    llm.queue(summary_reply(GOOD_BOOKED), summary_reply(LIAR), summary_reply(LIAR))
    runner, _ = _runner(sandbox, llm)

    report = await runner.run(_two_case_suite())

    failed = next(r for r in report.cases if r.case.id == "sum-failed-calendar-down")
    (turn,) = failed.playback.turns  # type: ignore[union-attr]
    assert turn.fallback == "template" and "nothing was booked" in turn.reply
    scores = {s.scorer: s.passed for s in failed.scores}
    assert scores == {"two_sentences": True, "matches_outcome": True, "ai_summary": False}
    assert report.model_calls == 3  # the rejected reply got exactly one retry


async def test_the_budget_stops_the_run_with_the_template_and_no_scoring():
    sandbox, llm = MemorySandbox(), FakeLLMClient()
    llm.queue(summary_reply(GOOD_BOOKED))
    limits = replace(LIMITS, max_requests=1)
    runner, _ = _runner(sandbox, llm, limits)

    report = await runner.run(_two_case_suite())

    assert report.stopped == "budget_exhausted"
    assert [r.status for r in report.cases] == ["scored", "not_run"]
    assert len(llm.requests) == 1


async def test_a_rate_limited_summary_is_backed_off_and_replayed():
    sandbox, llm = MemorySandbox(), FakeLLMClient()
    limited = LLMUnavailableError("429", retryable=False, rate_limited=True)
    llm.queue(limited, summary_reply(GOOD_BOOKED))
    runner, time = _runner(sandbox, llm)
    suite = _two_case_suite()
    suite = suite.model_copy(update={"cases": suite.cases[:1]})

    report = await runner.run(suite)

    assert report.cases[0].status == "scored" and report.cases[0].passed
    assert time.sleeps and report.stopped is None
