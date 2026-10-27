"""The `escalation` suite (slice 13): its shape (free-tier batches), its scorers, and a
run on the in-memory sandbox with a scripted fake model. No provider, no database."""

from collections import Counter

import pytest

from evals.cases import EvalCase, SuiteError, load_suite
from evals.playback import PlayedTurn
from evals.runner import EvalRunner, RunLimits
from evals.scorers import SCORERS
from integrations.llm.fake import FakeLLMClient, agent_turn
from tests.evals.memory_sandbox import MemorySandbox
from tests.evals.test_cases import write_suite

KNOWN = frozenset(SCORERS)
SUITE = load_suite("escalation", known_scorers=KNOWN)
POSITIVES = [c for c in SUITE.cases if c.expect_escalation]
NEGATIVES = [c for c in SUITE.cases if c.expect_escalation is False]
BATCHES = ("batch1", "batch2", "batch3")
LIMITS = RunLimits(
    max_requests=100,
    requests_per_minute=60,
    concurrency=1,
    rate_limit_backoff_seconds=30,
    max_rate_limit_retries=2,
)


# --- the committed suite ---------------------------------------------------------


def test_the_suite_meets_the_coverage_bar():
    assert len(POSITIVES) >= 20 and len(NEGATIVES) >= 10
    assert len(POSITIVES) + len(NEGATIVES) == len(SUITE.cases)
    reasons = {c.expected_reason for c in POSITIVES}
    assert reasons == {
        "urgent",
        "human_requested",
        "complaint",
        "change_request",
        "booking_unresolved",
        "integration_failing",
        "clarification_exhausted",
    }
    assert {m.scorer: m.target for m in SUITE.metrics}["escalation_recall"] == 0.95
    assert {m.scorer: m.target for m in SUITE.metrics}["no_callback_promise"] == 1.0
    assert all("no_callback_promise" in c.scorers for c in SUITE.cases)


def test_every_case_is_in_exactly_one_batch_and_smoke_is_small():
    for case in SUITE.cases:
        assert len(set(case.tags) & set(BATCHES)) == 1, case.id
    per_batch = Counter(t for c in SUITE.cases for t in c.tags if t in BATCHES)
    assert set(per_batch) == set(BATCHES) and max(per_batch.values()) <= 15
    smoke = [c for c in SUITE.cases if "smoke" in c.tags]
    assert 6 <= len(smoke) <= 10
    assert any(c.expect_escalation for c in smoke) and any(
        c.expect_escalation is False for c in smoke
    )


def test_multi_turn_cases_are_few():
    assert sum(len(c.turns) > 1 for c in SUITE.cases) <= 3


# --- case validation ---------------------------------------------------------------

HEADER = "name: t\nmetrics:\n  - {scorer: escalation_recall, label: recall}\n"


@pytest.mark.parametrize(
    ("case_yaml", "message"),
    [
        ("{id: a, turns: [x]}", "escalation_recall needs expect_escalation: true"),
        ("{id: a, turns: [x], expect_escalation: false}", "escalation_recall needs"),
        (
            "{id: a, turns: [x], expect_escalation: true, expected_reason: nope}",
            "expected_reason",
        ),
    ],
)
def test_bad_escalation_cases_fail_at_load(tmp_path, case_yaml, message):
    path = write_suite(tmp_path, f"scorers: [escalation_recall]\ncases:\n  - {case_yaml}\n", HEADER)
    with pytest.raises(SuiteError, match=message):
        load_suite(path, known_scorers=KNOWN)


def test_reason_match_and_false_escalation_need_their_keys(tmp_path):
    path = write_suite(
        tmp_path,
        "scorers: [reason_match]\ncases:\n  - {id: a, turns: [x], expect_escalation: true}\n",
        HEADER,
    )
    with pytest.raises(SuiteError, match="reason_match needs expected_reason"):
        load_suite(path, known_scorers=KNOWN)
    path = write_suite(
        tmp_path,
        "scorers: [false_escalation]\ncases:\n  - {id: a, turns: [x], expect_escalation: true}\n",
        "name: t\nmetrics:\n  - {scorer: false_escalation, label: fe}\n",
    )
    with pytest.raises(SuiteError, match="false_escalation needs expect_escalation: false"):
        load_suite(path, known_scorers=KNOWN)


# --- scorers ------------------------------------------------------------------------


def turn(reply="Sure.", escalations=()) -> PlayedTurn:
    return PlayedTurn(
        customer="hi",
        reply=reply,
        intent="UNKNOWN",
        model_intent=None,
        sources=(),
        fallback=None,
        operations=(),
        model_calls=1,
        rate_limited=False,
        escalations=tuple(escalations),
    )


def case(**fields) -> EvalCase:
    return EvalCase(id="c", turns=("hi",), **fields)


def run(name, case_, *turns):
    return SCORERS[name].score(case_, turns, ())


def test_recall_needs_any_escalation_record():
    c = case(expect_escalation=True)
    assert run("escalation_recall", c, turn(escalations=[("complaint", "normal")])).passed
    missed = run("escalation_recall", c, turn())
    assert not missed.passed and missed.detail == "no escalation recorded"


def test_recall_reads_the_final_state_of_a_multi_turn_case():
    c = case(expect_escalation=True)
    assert run("escalation_recall", c, turn(), turn(escalations=[("urgent", "high")])).passed


def test_reason_match_is_about_the_expected_reason():
    c = case(expect_escalation=True, expected_reason="urgent")
    assert run("reason_match", c, turn(escalations=[("urgent", "high")])).passed
    wrong = run("reason_match", c, turn(escalations=[("complaint", "normal")]))
    assert not wrong.passed and "recorded complaint" in wrong.detail
    assert "none" in run("reason_match", c, turn()).detail


def test_false_escalation_fails_on_any_record():
    c = case(expect_escalation=False)
    assert run("false_escalation", c, turn()).passed
    bad = run("false_escalation", c, turn(escalations=[("change_request", "normal")]))
    assert not bad.passed and "change_request" in bad.detail


@pytest.mark.parametrize(
    ("reply", "passed"),
    [
        ("I've passed this on to the team. You can also call us on 555 0100.", True),
        ("Someone will call you back shortly.", False),
        ("The owner will get back to you tomorrow.", False),
        ("Would you like the owner to call you?", True),
    ],
)
def test_no_callback_promise_uses_the_grounding_detector(reply, passed):
    assert run("no_callback_promise", case(), turn(reply)).passed is passed


def test_no_callback_promise_checks_every_turn_and_must_not_contain():
    c = case(must_not_contain=("refund is approved",))
    assert not run("no_callback_promise", c, turn("Someone will call you."), turn()).passed
    bad = run("no_callback_promise", c, turn(), turn("Your refund is approved."))
    assert not bad.passed and "turn 2" in bad.detail


# --- the suite through the runner on the in-memory sandbox --------------------------


def runner(sandbox, llm) -> EvalRunner:
    return EvalRunner(sandbox, llm, LIMITS, model="fake-model", tier="free")


async def test_a_human_request_is_recalled_and_a_faq_question_is_not_escalated():
    human = next(c for c in POSITIVES if c.id == "esc-human-real-person")
    thanks = next(c for c in NEGATIVES if c.id == "esc-no-thanks-bye")
    sandbox, llm = MemorySandbox(), FakeLLMClient()
    llm.queue(
        agent_turn(
            "I've let the team know you'd like to speak with someone.", intent="HUMAN_REQUEST"
        ),
        agent_turn("You're welcome, bye!", intent="UNKNOWN"),
    )
    suite = SUITE.model_copy(update={"cases": (human, thanks)})

    report = await runner(sandbox, llm).run(suite)

    assert all(c.passed for c in report.cases), [c.scores for c in report.cases]
    first = report.cases[0].playback
    assert first is not None and first.turns[-1].escalations == (("human_requested", "normal"),)
    second = report.cases[1].playback
    assert second is not None and second.turns[-1].escalations == ()
    assert len(llm.requests) == 2  # no extra model call for the escalation
    by_scorer = {m.scorer: (m.passed, m.total) for m in report.metrics}
    assert by_scorer["escalation_recall"] == (1, 1)
    assert by_scorer["false_escalation"] == (1, 1)


async def test_a_missed_escalation_and_a_false_one_are_reported():
    urgent = next(c for c in POSITIVES if c.id == "esc-complaint-formal")
    cancel = next(c for c in NEGATIVES if c.id == "esc-no-cancellation-policy")
    sandbox, llm = MemorySandbox(), FakeLLMClient()
    # The model labels the complaint FAQ (the customer's words still trip the policy) ...
    llm.queue(agent_turn("Thanks for telling me.", intent="UNKNOWN"))
    # ... and reads the policy question as a cancel request.
    llm.queue(agent_turn("I can't cancel appointments myself.", intent="CANCEL"))
    report = await runner(sandbox, llm).run(SUITE.model_copy(update={"cases": (urgent, cancel)}))

    complaint, policy = report.cases
    assert complaint.passed  # "file a complaint" is caught by the backend rule, not the label
    assert not policy.passed
    assert any(s.scorer == "false_escalation" and not s.passed for s in policy.scores)
