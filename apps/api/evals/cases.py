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

from collections.abc import Hashable
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from schemas.agent_turn import Intent
from services.escalation_policy import Reason

SUITES_DIR = Path(__file__).parent / "suites"
# Scorers that judge a stored interaction's summary (`evals/scorers.py`), not a chat.
SUMMARY_SCORERS = frozenset({"two_sentences", "matches_outcome", "ai_summary"})
SUITE_FILE = "suite.yaml"
MAX_TURNS = 10

CaseId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
Utterance = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Phrase = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Tag = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")]
ClockTime = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]
IsoDate = Annotated[
    str, StringConstraints(pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
]

# The scripted Google Calendar a case runs against (`evals/sandbox.py`):
# open (nothing busy), busy (a realistic partly booked week), fully_booked (every open
# hour taken), down (Google fails every call), disconnected (no connection row),
# booking_fails (the `busy` week, but creating an event fails every time).
CalendarMode = Literal["open", "busy", "fully_booked", "down", "disconnected", "booking_fails"]


Speaker = Literal["agent", "customer"]
LocalDateTime = Annotated[
    str,
    StringConstraints(
        pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01]) ([01]\d|2[0-3]):[0-5]\d$"
    ),
]


class StoredMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    speaker: Speaker
    text: Utterance


class StoredOperation(BaseModel):
    """One operation-log row of the seeded interaction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str = Field(pattern=r"^[a-z_]{1,40}$")
    status: Literal["succeeded", "rejected", "failed"] = "succeeded"
    # The machine result code in the log (`confirmed`, `booking_failed`, ...).
    result: str | None = Field(default=None, pattern=r"^[a-z_]{1,40}$")


class StoredBooking(BaseModel):
    """A booking row of the seeded interaction. Every `confirmed` one of a run lands in
    the same sandbox tenant, whose double-booking guard is real: use a distinct slot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    service: str = Field(min_length=1, max_length=60)
    # The business's local time ("2026-06-05 14:15"); the sandbox converts it to UTC.
    start: LocalDateTime
    status: Literal["confirmed", "failed"]


class StoredInteraction(BaseModel):
    """A finished conversation as the database would hold it, for the `summary` suite:
    the case is one `Summarizer` call over this, not a played chat."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: Intent | None = None
    status: Literal["completed", "failed", "abandoned"]
    outcome: Literal["booked", "answered", "escalated", "failed", "abandoned"]
    requires_human: bool = False
    messages: tuple[StoredMessage, ...] = Field(min_length=1, max_length=20)
    operations: tuple[StoredOperation, ...] = ()
    bookings: tuple[StoredBooking, ...] = ()


class SuiteError(ValueError):
    """A suite or case file is missing or invalid."""


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: CaseId
    # Customer utterances played as a chat. Empty only for a `stored` (summary) case.
    turns: tuple[Utterance, ...] = Field(default=(), max_length=MAX_TURNS)
    # For the `summary` suite: a stored interaction to summarize instead of a chat to play.
    stored: StoredInteraction | None = None
    expected_intent: Intent | None = None
    # None: not checked. A list: each tool must have run successfully during the case.
    expected_tools: tuple[str, ...] | None = None
    must_contain: tuple[Phrase, ...] = ()
    must_not_contain: tuple[Phrase, ...] = ()
    tags: tuple[Tag, ...] = ()
    # For `tools_not_used` (added automatically): none of these may be called (any status).
    forbidden_tools: tuple[str, ...] = ()
    calendar: CalendarMode = "busy"
    # For `offers_real_times`: local HH:MM starts the scripted calendar really has free
    # (what `check_availability` could offer). None: not used.
    expected_times: tuple[ClockTime, ...] | None = None
    # For `offers_real_times`: the reply may state no time of day at all.
    no_times: bool = False
    # For `books_correctly`: whether the case must end with a confirmed booking. When true,
    # `expected_start` (local HH:MM) and `expected_date` (YYYY-MM-DD) say which one.
    expect_booking: bool | None = None
    expected_start: ClockTime | None = None
    expected_date: IsoDate | None = None
    # For `books_correctly` with `expect_booking: false`: the result the LAST `create_booking`
    # call must have returned (e.g. `booking_failed`), so a case can't pass for another reason.
    expected_booking_result: (
        Literal["slot_taken", "invalid", "booking_failed", "ambiguous_service", "service_not_found"]
        | None
    ) = None
    # For `escalation_recall` (true) and `false_escalation` (false): whether the case must
    # leave an escalation record on its interaction. `expected_reason` (true only) is the
    # reason `reason_match` checks, information beyond recall.
    expect_escalation: bool | None = None
    expected_reason: Reason | None = None
    # Filled from the case file's `scorers`; a case may add its own.
    scorers: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _chat_or_stored(self) -> "EvalCase":
        if bool(self.turns) == (self.stored is not None):
            raise ValueError("a case has either turns (a chat) or stored (a summary), not both.")
        return self


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


class _UniqueKeyLoader(yaml.SafeLoader):
    """`SafeLoader` that refuses a repeated mapping key. Plain YAML keeps only the
    last one, so a case with two `must_not_contain:` would silently lose a check."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        seen: set[object] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)  # pyright: ignore[reportArgumentType]
            if not isinstance(key, Hashable):  # e.g. `? [a, b]`: a refused file, not a crash
                raise yaml.constructor.ConstructorError(
                    None, None, "unhashable key", key_node.start_mark
                )
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate key {key!r}", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _read_yaml(path: Path) -> object:
    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
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
            if case.forbidden_tools:
                implied = (*implied, "tools_not_used")
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
        summary_scorers = set(case.scorers) & SUMMARY_SCORERS
        if bool(summary_scorers) != (case.stored is not None):
            raise SuiteError(f"{case.id}: summary scorers go with `stored` cases, and only those.")
        if "intent_match" in case.scorers and case.expected_intent is None:
            raise SuiteError(f"{case.id}: intent_match needs expected_intent.")
        if "offers_real_times" in case.scorers and bool(case.expected_times) == case.no_times:
            raise SuiteError(
                f"{case.id}: offers_real_times needs expected_times or no_times (not both)."
            )
        if "books_correctly" in case.scorers:
            if case.expect_booking is None:
                raise SuiteError(f"{case.id}: books_correctly needs expect_booking.")
            has_target = case.expected_start is not None and case.expected_date is not None
            if case.expect_booking != has_target or (
                not case.expect_booking
                and (case.expected_start is not None or case.expected_date is not None)
            ):
                raise SuiteError(
                    f"{case.id}: expected_start and expected_date go with expect_booking: true "
                    "(both), and only with it."
                )
            if case.expected_booking_result is not None and case.expect_booking:
                raise SuiteError(
                    f"{case.id}: expected_booking_result goes with expect_booking: false."
                )
        if "escalation_recall" in case.scorers and case.expect_escalation is not True:
            raise SuiteError(f"{case.id}: escalation_recall needs expect_escalation: true.")
        if "false_escalation" in case.scorers and case.expect_escalation is not False:
            raise SuiteError(f"{case.id}: false_escalation needs expect_escalation: false.")
        if "reason_match" in case.scorers and case.expected_reason is None:
            raise SuiteError(f"{case.id}: reason_match needs expected_reason.")
        if case.expected_reason is not None and case.expect_escalation is not True:
            raise SuiteError(f"{case.id}: expected_reason goes with expect_escalation: true.")
    used = {scorer for case in suite.cases for scorer in case.scorers}
    for metric in suite.metrics:
        if metric.scorer not in known_scorers:
            raise SuiteError(f"Metric {metric.label!r}: unknown scorer {metric.scorer!r}.")
        # Otherwise a target would be "met" by zero cases. (A `--tag` subset may still
        # leave a metric empty: it is reported n/a and never counts as met.)
        if metric.scorer not in used:
            raise SuiteError(f"Metric {metric.label!r}: no case uses {metric.scorer!r}.")
