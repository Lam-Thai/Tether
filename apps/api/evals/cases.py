"""Eval suites as data: YAML case files under `evals/suites/<name>/`, validated strictly.

A suite directory holds `suite.yaml` (name, description, the metrics it reports
and their targets) and any number of case files:

    # evals/suites/core/faq_grounding.yaml
    scorers: [grounded_in_faq]        # applied to every case in this file
    cases:
      - id: faq-walk-in
        turns: ["can I just walk in?"]   # customer utterances, played in order
        expected_intent: FAQ             # the last turn's intent
        expected_tools: [search_faq]     # must each have run (succeeded); adds `tools_used`
        must_contain: ["3"]              # in the last reply, ignoring case
        must_not_contain: ["appointment only"]
        tags: [walk-in]

Unknown keys, duplicate ids, unknown scorers and unknown intents are errors,
so a typo fails the run before any model call is spent. Cases are synthetic:
the utterances are written for the demo salon (`seeds/demo_salon.py`), never
copied from real customers.
"""

from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from schemas.agent_turn import Intent

SUITES_DIR = Path(__file__).parent / "suites"
SUITE_FILE = "suite.yaml"
MAX_TURNS = 10

CaseId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
Utterance = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Phrase = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Tag = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")]


class SuiteError(ValueError):
    """A suite or case file is missing or invalid."""


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: CaseId
    turns: tuple[Utterance, ...] = Field(min_length=1, max_length=MAX_TURNS)
    expected_intent: Intent | None = None
    # None: not checked. A list: each tool must have run successfully during the case.
    expected_tools: tuple[str, ...] | None = None
    must_contain: tuple[Phrase, ...] = ()
    must_not_contain: tuple[Phrase, ...] = ()
    tags: tuple[Tag, ...] = ()
    # Filled from the case file's `scorers`; a case may add its own.
    scorers: tuple[str, ...] = ()


class Metric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scorer: str
    label: str = Field(min_length=1, max_length=40)
    # None: reported for information, never fails the run.
    target: float | None = Field(default=None, ge=0, le=1)


class Suite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str = ""
    metrics: tuple[Metric, ...] = Field(min_length=1)
    cases: tuple[EvalCase, ...] = Field(min_length=1)


class _SuiteFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    metrics: tuple[Metric, ...] = Field(min_length=1)


class _CaseFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scorers: tuple[str, ...] = Field(min_length=1)
    cases: tuple[EvalCase, ...] = Field(min_length=1)


def _read_yaml(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SuiteError(f"{path.name}: cannot read ({type(exc).__name__}).") from exc


def load_suite(name_or_path: str | Path, *, known_scorers: frozenset[str]) -> Suite:
    """A suite by name (`core` → `evals/suites/core/`) or by directory path.

    Case files are read in name order and cases keep their file order, so the
    same suite always plays the same cases in the same order.
    """
    # A bare name is a suite under `evals/suites/` (cwd may hold an app package called `core`).
    named = SUITES_DIR / str(name_or_path)
    directory = named if (named / SUITE_FILE).is_file() else Path(name_or_path)
    suite_file = directory / SUITE_FILE
    if not suite_file.is_file():
        raise SuiteError(f"No suite at {directory.name!r} (missing {SUITE_FILE}).")
    try:
        header = _SuiteFile.model_validate(_read_yaml(suite_file))
    except ValueError as exc:
        raise SuiteError(f"{SUITE_FILE}: {exc}") from exc

    cases: list[EvalCase] = []
    for path in sorted(directory.glob("*.yaml")):
        if path.name == SUITE_FILE:
            continue
        try:
            parsed = _CaseFile.model_validate(_read_yaml(path))
        except ValueError as exc:
            raise SuiteError(f"{path.name}: {exc}") from exc
        for case in parsed.cases:
            implied = ("tools_used",) if case.expected_tools is not None else ()
            scorers = tuple(dict.fromkeys((*parsed.scorers, *case.scorers, *implied)))
            cases.append(case.model_copy(update={"scorers": scorers}))

    if not cases:
        raise SuiteError(f"Suite {header.name!r} has no cases.")
    suite = Suite(
        name=header.name, description=header.description, metrics=header.metrics, cases=tuple(cases)
    )
    _check(suite, known_scorers)
    return suite


def _check(suite: Suite, known_scorers: frozenset[str]) -> None:
    seen: set[str] = set()
    for case in suite.cases:
        if case.id in seen:
            raise SuiteError(f"Duplicate case id {case.id!r}.")
        seen.add(case.id)
        unknown = set(case.scorers) - known_scorers
        if unknown:
            raise SuiteError(f"{case.id}: unknown scorer(s) {sorted(unknown)}.")
        if "intent_match" in case.scorers and case.expected_intent is None:
            raise SuiteError(f"{case.id}: intent_match needs expected_intent.")
    used = {scorer for case in suite.cases for scorer in case.scorers}
    for metric in suite.metrics:
        if metric.scorer not in known_scorers:
            raise SuiteError(f"Metric {metric.label!r}: unknown scorer {metric.scorer!r}.")
        # Otherwise a target would be "met" by zero cases. (A `--tag` subset may still
        # leave a metric empty: it is reported n/a and never counts as met.)
        if metric.scorer not in used:
            raise SuiteError(f"Metric {metric.label!r}: no case uses {metric.scorer!r}.")
