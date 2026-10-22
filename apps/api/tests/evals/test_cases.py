"""Suite loading, and the shape of the committed `core` suite (issue #8's coverage bar)."""

from collections import Counter
from pathlib import Path
from typing import get_args

import pytest

from evals.cases import SuiteError, load_suite
from evals.scorers import SCORERS
from schemas.agent_turn import Intent

KNOWN = frozenset(SCORERS)

SUITE_YAML = """\
name: tiny
metrics:
  - scorer: intent_match
    label: intent accuracy
    target: 0.9
"""


def write_suite(tmp_path: Path, cases_yaml: str, suite_yaml: str = SUITE_YAML) -> Path:
    (tmp_path / "suite.yaml").write_text(suite_yaml, encoding="utf-8")
    (tmp_path / "cases.yaml").write_text(cases_yaml, encoding="utf-8")
    return tmp_path


def test_loads_cases_with_the_file_scorers_and_implied_tools_used(tmp_path):
    path = write_suite(
        tmp_path,
        """\
scorers: [intent_match]
cases:
  - id: one
    turns: ["hi", "where are you?"]
    expected_intent: LOCATION
    expected_tools: [get_business_info]
    tags: [location]
""",
    )
    suite = load_suite(path, known_scorers=KNOWN)
    (case,) = suite.cases
    assert suite.name == "tiny" and case.turns == ("hi", "where are you?")
    assert case.scorers == ("intent_match", "tools_used")


@pytest.mark.parametrize(
    ("cases_yaml", "message"),
    [
        ("scorers: [intent_match]\ncases:\n  - {id: a, turns: [x], expected_intent: NOPE}\n",
         "expected_intent"),
        ("scorers: [intent_match]\ncases:\n  - {id: a, turns: [x], typo: 1}\n", "typo"),
        ("scorers: [made_up]\ncases:\n  - {id: a, turns: [x], expected_intent: FAQ}\n",
         "unknown scorer"),
        ("scorers: [intent_match]\ncases:\n  - {id: a, turns: [x]}\n", "needs expected_intent"),
        ("scorers: [intent_match]\ncases:\n  - {id: a, turns: [], expected_intent: FAQ}\n",
         "turns"),
        ("scorers: [intent_match]\ncases:\n"
         "  - {id: a, turns: [x], expected_intent: FAQ}\n"
         "  - {id: a, turns: [y], expected_intent: FAQ}\n", "Duplicate case id"),
        ("scorers: [intent_match]\ncases: [\n", "cannot read"),
        # A repeated key would silently drop the first list of checks.
        ("scorers: [intent_match]\ncases:\n  - id: a\n    turns: [x]\n    expected_intent: FAQ\n"
         "    must_not_contain: [one]\n    must_not_contain: [two]\n", "cannot read"),
        # A sequence as a key (`? [a, b]`) is refused, not a TypeError traceback.
        ("scorers: [intent_match]\ncases:\n  - id: a\n    ? [x, y]\n    : z\n", "cannot read"),
    ],
)
def test_invalid_case_files_are_refused_before_any_model_call(tmp_path, cases_yaml, message):
    path = write_suite(tmp_path, cases_yaml)
    with pytest.raises(SuiteError, match=message):
        load_suite(path, known_scorers=KNOWN)


def test_a_metric_no_case_uses_is_refused(tmp_path):
    path = write_suite(
        tmp_path,
        "scorers: [grounded_in_faq]\ncases:\n  - {id: a, turns: [x], must_contain: [y]}\n",
    )
    with pytest.raises(SuiteError, match="no case uses 'intent_match'"):
        load_suite(path, known_scorers=KNOWN)


def test_a_missing_suite_is_refused(tmp_path):
    with pytest.raises(SuiteError, match="missing suite.yaml"):
        load_suite(tmp_path / "nope", known_scorers=KNOWN)


def test_core_suite_covers_every_intent_and_faq_grounding():
    suite = load_suite("core", known_scorers=KNOWN)
    intent_cases = [c for c in suite.cases if "intent_match" in c.scorers]
    single_turn = [c for c in intent_cases if len(c.turns) == 1]
    assert len(single_turn) >= 60
    covered = Counter(c.expected_intent for c in single_turn)
    assert set(covered) == set(get_args(Intent))
    assert min(covered.values()) >= 4  # paraphrases, not one example per intent

    grounding = [c for c in suite.cases if "grounded_in_faq" in c.scorers]
    assert len(grounding) >= 20
    assert sum(1 for c in grounding if not c.must_contain) >= 5  # questions with no answer
    # The free tier's daily run: a subset that fits about 20 requests.
    smoke = [c for c in suite.cases if "smoke" in c.tags]
    assert 8 <= len(smoke) <= 12
    assert {m.scorer: m.target for m in suite.metrics if m.target is not None} == {
        "intent_match": 0.90,
        "grounded_in_faq": 0.95,
        "no_false_confirmation": 1.0,
    }
    # Booking-type intents are also checked for false confirmations.
    for case in suite.cases:
        if case.expected_intent in ("BOOK_APPOINTMENT", "RESCHEDULE", "CANCEL"):
            assert "no_false_confirmation" in case.scorers, case.id
