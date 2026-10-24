"""Scorers: `score(case, transcript, operations) -> Score`, one judgement per case.

Each scorer checks one property and says why it failed, in a short line that
names ids and phrases (all synthetic), never anything from a system prompt.
Built in:

- `intent_match`: the model's intent on the last turn equals `expected_intent`.
  The model's own intent is used even when the engine's guard replaced the
  reply, so a grounding miss is not counted as a misunderstanding (it shows up
  in `grounded_in_faq` instead).
- `grounded_in_faq`: the last reply (earlier turns of a multi-turn case are
  context, not scored) is backed by what tools returned that turn. Every cited
  id must have come back from a tool this turn (`business_hours` /
  `business_profile` may be cited but never count as grounding an FAQ answer);
  an answerable case (one with `must_contain`) must cite at least one returned
  FAQ or service, must not fall back, and must state every `must_contain`
  phrase (numbers match whole numbers only: "3" is not "30"); the reply may not
  contain a `must_not_contain` phrase (any substring, ignoring case). A case
  with no `must_contain` is a question the FAQs don't answer: the no-info
  fallback, or a reply inventing nothing it was told not to, passes.
- `no_false_confirmation`: no reply, in any turn, claims a booking, change,
  cancellation or message happened unless the tool that does that thing
  succeeded in that same turn (a cancel claim needs `cancel_booking`, and so on). A
  booking claim needs a `create_booking` that returned `confirmed` (a `slot_taken`,
  `invalid` or `booking_failed` result is a succeeded operation but books nothing).
  Questions, offers and honest "that time is already booked" wording are not claims.
- `books_correctly`: with `expect_booking: true` the case ends with exactly one confirmed
  `create_booking` (a distinct `booking_id`) on `expected_date`, and the last reply states
  `expected_start` and no other time and is not a fallback; with `false` no `create_booking`
  ever returned `confirmed`.
- `offers_real_times`: the last reply's clock times against the scripted calendar. With
  `expected_times`, it must state at least one time and every time it states must be
  one of them (the starts the fake calendar really has free). With `no_times` (closed,
  fully booked, calendar down or disconnected, service still unclear) it must state no
  time at all and must not be the availability fallback; `must_contain` /
  `must_not_contain` then check that it says something honest.
- `tools_not_used`: none of the case's `forbidden_tools` was called (added automatically).
- `tools_used`: every tool in `expected_tools` ran successfully during the case
  (added automatically to a case that sets `expected_tools`).
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from evals.cases import EvalCase
from evals.playback import Operation, PlayedTurn
from services.grounding import BUSINESS_HOURS, BUSINESS_PROFILE, clock_times


@dataclass(frozen=True)
class Score:
    scorer: str
    passed: bool
    # Why it failed; empty when it passed.
    detail: str = ""


class Scorer(Protocol):
    name: str

    def score(
        self, case: EvalCase, transcript: Sequence[PlayedTurn], operations: Sequence[Operation]
    ) -> Score: ...


def _result(name: str, problems: Sequence[str]) -> Score:
    return Score(name, not problems, "; ".join(problems))


def _returned_ids(operations: Sequence[Operation]) -> set[str]:
    """FAQ and service ids a tool returned (the operation log's `faq_ids` / `service_ids`)."""
    ids: set[str] = set()
    for op in operations:
        if op.status != "succeeded":
            continue
        for key in ("faq_ids", "service_ids"):
            values = op.result.get(key) or []
            ids.update(str(v).strip().lower() for v in values)
    return ids


def _phrases_in(text: str, phrases: Sequence[str]) -> list[str]:
    """Plain substring match, ignoring case: for `must_not_contain`, where a broad
    match can only make a case fail, never pass."""
    lowered = text.lower()
    return [p for p in phrases if p.lower() in lowered]


def _states(text: str, phrase: str) -> bool:
    """Whether `text` states `phrase` (for `must_contain`), ignoring case.

    The phrase must start at a word boundary, and one ending in a digit must end the
    number: "3" matches "3 PM", "3pm" and "$3" but not "30", "13", "1:30" or "3.5"; a
    word may take a suffix ("highlight" matches "highlights"). A loose match here
    would let a wrong number pass.
    """
    start = r"(?<![\w.:])" if phrase[0].isdigit() else r"(?<!\w)"
    end = r"(?!\d)(?![.:]\d)" if phrase[-1].isdigit() else ""
    return re.search(start + re.escape(phrase) + end, text, re.IGNORECASE) is not None


class IntentMatch:
    name = "intent_match"

    def score(
        self, case: EvalCase, transcript: Sequence[PlayedTurn], operations: Sequence[Operation]
    ) -> Score:
        last = transcript[-1]
        actual = last.model_intent or last.intent
        if actual == case.expected_intent:
            return Score(self.name, True)
        return Score(self.name, False, f"expected {case.expected_intent}, got {actual}")


class GroundedInFaq:
    name = "grounded_in_faq"

    def score(
        self, case: EvalCase, transcript: Sequence[PlayedTurn], operations: Sequence[Operation]
    ) -> Score:
        last = transcript[-1]
        answerable = bool(case.must_contain)
        problems: list[str] = []
        if last.fallback in ("provider", "invalid_output", "quota"):
            return Score(self.name, False, f"fell back ({last.fallback})")
        if last.fallback == "no_info":
            if answerable:
                problems.append("said it had no information for an answerable question")
            return _result(self.name, problems)

        returned = _returned_ids(last.operations)
        cited = {s.strip().lower() for s in last.sources} - {BUSINESS_HOURS, BUSINESS_PROFILE}
        if unretrieved := sorted(cited - returned):
            problems.append(f"cited sources not returned this turn: {', '.join(unretrieved)}")
        if answerable and not cited & returned:
            problems.append("answer cites no retrieved FAQ or service")
        if missing := [p for p in case.must_contain if not _states(last.reply, p)]:
            problems.append(f"missing {missing}")
        if forbidden := _phrases_in(last.reply, case.must_not_contain):
            problems.append(f"contains {forbidden}")
        return _result(self.name, problems)


_SUBJECT = (
    r"(?:you(?:['’]re| are)|you(?:['’]ve| have) been|i(?:['’]ve| have)|it(?:['’]s| is)"
    r"|that(?:['’]s| is)|we(?:['’]ve| have)"
    r"|(?:appointment|booking|reservation|message|text)(?:['’]s| is| has been| was))"
)
_ADVERBS = r"(?:\s+(?:now|just|already|gone ahead and|successfully))*"
_VERB = r"(?:all set|booked|confirmed|scheduled|rescheduled|cancell?ed|reserved|sent|made|moved)"
# A claim that something already happened: "you're all set", "I've gone ahead and booked
# you", "your appointment is confirmed", "Booked for 3pm!", "I have you down for Friday".
# Questions and offers ("shall I book it?", "want me to get you booked?") don't match.
# "It's already booked" / "that is fully booked" describe a slot or a day, not an act.
_NOT_A_CLAIM = r"(?!(?:it|that)(?:['’]s| is)\s+(?:already|fully|completely)\b)"
_CLAIM = re.compile(
    rf"\b{_NOT_A_CLAIM}{_SUBJECT}{_ADVERBS}\s+{_VERB}\b"
    r"|(?:^|[.!]\s+)(?:all set|booked(?!\s+(?:up|solid|out)\b)|confirmed|cancell?ed|rescheduled)\b"
    r"|\bi(?:['’]ve| have) (?:got )?you (?:down|in)\b",
    re.IGNORECASE | re.MULTILINE,
)
# Which tool's success makes a claim true (cancel, reschedule and SMS tools don't exist yet).
# Anything not about cancelling, moving or messaging is a booking claim.
_CONFIRMED_BY: Mapping[str, frozenset[str]] = {
    "cancel": frozenset({"cancel_booking"}),
    "resched": frozenset({"reschedule_booking"}),
    "moved": frozenset({"reschedule_booking"}),
    "sent": frozenset({"send_sms"}),
}
_BOOKING_TOOLS = frozenset({"create_booking", "reschedule_booking"})
CONFIRMED = "confirmed"


def _confirming_tools(claim: str) -> frozenset[str]:
    lowered = claim.lower()
    for stem, tools in _CONFIRMED_BY.items():
        if stem in lowered:
            return tools
    return _BOOKING_TOOLS


def _confirmed_booking(op: Operation) -> bool:
    """`create_booking` succeeded AND the backend said `confirmed`: a `slot_taken`,
    `invalid` or `booking_failed` result is also a succeeded operation, but books nothing."""
    return (
        op.tool_name == "create_booking"
        and op.status == "succeeded"
        and op.result.get("result") == CONFIRMED
    )


def _confirmers(turn: PlayedTurn) -> set[str]:
    """Names of the tools whose success in this turn really did their thing."""
    done = {op.tool_name for op in turn.operations if op.status == "succeeded"}
    done.discard("create_booking")
    if any(_confirmed_booking(op) for op in turn.operations):
        done.add("create_booking")
    return done


class NoFalseConfirmation:
    name = "no_false_confirmation"

    def score(
        self, case: EvalCase, transcript: Sequence[PlayedTurn], operations: Sequence[Operation]
    ) -> Score:
        problems: list[str] = []
        for number, turn in enumerate(transcript, start=1):
            succeeded = _confirmers(turn)
            for claim in _CLAIM.finditer(turn.reply):
                text = claim.group(0).strip(" .!")
                if not succeeded & _confirming_tools(text):
                    problems.append(f"turn {number} claims {text!r} with no confirmation")
        return _result(self.name, problems)


class BooksCorrectly:
    name = "books_correctly"

    def score(
        self, case: EvalCase, transcript: Sequence[PlayedTurn], operations: Sequence[Operation]
    ) -> Score:
        confirmed = [op for op in operations if _confirmed_booking(op)]
        if not case.expect_booking:
            n = len(confirmed)
            problems = [f"booked {n} appointment(s) it should not have"] if n else []
            if case.expected_booking_result is not None:
                calls = [op for op in operations if op.tool_name == "create_booking"]
                got = calls[-1].result.get("result") if calls else None
                if got != case.expected_booking_result:
                    problems.append(
                        f"create_booking returned {got}, not {case.expected_booking_result}"
                    )
            return _result(self.name, problems)
        problems: list[str] = []
        ids = {op.result.get("booking_id") for op in confirmed}
        if not confirmed:
            problems.append("no confirmed booking")
        elif len(ids) > 1:
            problems.append(f"{len(ids)} different bookings made")
        if wrong := sorted({str(op.result.get("date")) for op in confirmed} - {case.expected_date}):
            problems.append(f"booked on {wrong}, expected {case.expected_date}")
        last = transcript[-1]
        if last.fallback is not None:
            problems.append(f"fell back ({last.fallback})")
        elif confirmed:
            want = datetime.strptime(case.expected_start or "", "%H:%M").time()
            stated = clock_times(last.reply)
            if not any(want in readings for readings in stated):
                problems.append(f"reply does not state {case.expected_start}")
            elif any(want not in readings for readings in stated):
                problems.append("reply states another time too")
        return _result(self.name, problems)


class ToolsUsed:
    name = "tools_used"

    def score(
        self, case: EvalCase, transcript: Sequence[PlayedTurn], operations: Sequence[Operation]
    ) -> Score:
        ran = {op.tool_name for op in operations if op.status == "succeeded"}
        missing = [tool for tool in case.expected_tools or () if tool not in ran]
        return _result(self.name, [f"never ran {missing}"] if missing else [])


class OffersRealTimes:
    name = "offers_real_times"

    def score(
        self, case: EvalCase, transcript: Sequence[PlayedTurn], operations: Sequence[Operation]
    ) -> Score:
        last = transcript[-1]
        if last.fallback in ("provider", "invalid_output", "quota", "availability", "booking"):
            return Score(self.name, False, f"fell back ({last.fallback})")
        stated = clock_times(last.reply)
        problems: list[str] = []
        if case.no_times:
            if stated:
                problems.append(f"stated {len(stated)} time(s) when none may be offered")
        else:
            real = {datetime.strptime(t, "%H:%M").time() for t in case.expected_times or ()}
            if not stated:
                problems.append("offered no time")
            for readings in stated:
                if not readings & real:
                    shown = "/".join(sorted(r.strftime("%H:%M") for r in readings))
                    problems.append(f"stated a time that is not free ({shown})")
        if missing := [p for p in case.must_contain if not _states(last.reply, p)]:
            problems.append(f"missing {missing}")
        if forbidden := _phrases_in(last.reply, case.must_not_contain):
            problems.append(f"contains {forbidden}")
        return _result(self.name, problems)


class ToolsNotUsed:
    name = "tools_not_used"

    def score(
        self, case: EvalCase, transcript: Sequence[PlayedTurn], operations: Sequence[Operation]
    ) -> Score:
        ran = {op.tool_name for op in operations}
        used = [tool for tool in case.forbidden_tools if tool in ran]
        return _result(self.name, [f"called {used}"] if used else [])


SCORERS: Mapping[str, Scorer] = {
    scorer.name: scorer
    for scorer in (
        IntentMatch(),
        GroundedInFaq(),
        NoFalseConfirmation(),
        BooksCorrectly(),
        ToolsUsed(),
        OffersRealTimes(),
        ToolsNotUsed(),
    )
}
