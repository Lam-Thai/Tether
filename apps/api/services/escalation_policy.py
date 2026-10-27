"""`EscalationPolicy`: does this turn need a person, and why? (slice 13)

`evaluate(state, turn, operations, rules) -> EscalationDecision | None` is PURE: no
I/O, no clock, no model call. It combines deterministic rules applied in code with
the model's own classification (`turn.intent`, `turn.model_requires_human`), which
is one input and never the only signal for a reason a rule can see.

Reasons, highest priority first (one decision per turn; the top reason wins):

- `urgent`: an emergency or safety risk, or one of the owner's urgent keywords.
- `human_requested`: the customer asked for a person.
- `complaint`: the customer is complaining.
- `change_request`: reschedule / cancel an existing appointment (slice 11 routes here;
  the agent can't do it itself).
- `booking_unresolved`: a booking attempt failed in this interaction.
- `integration_failing`: the same integration failed repeatedly in this interaction.
- `clarification_exhausted`: the agent couldn't answer after the owner's configured
  number of attempts.

The engine (`services/conversation.py`) calls it once per turn, after the reply is
decided and before it is stored; `services/escalation.EscalationService` records the
decision. `needs_emergency_pointer` / `EMERGENCY_REPLY` back the urgent safety path:
an urgent turn whose reply doesn't send the customer to emergency services is
replaced by the fixed line.
"""

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal

Reason = Literal[
    "urgent",
    "human_requested",
    "complaint",
    "change_request",
    "booking_unresolved",
    "integration_failing",
    "clarification_exhausted",
]
Urgency = Literal["normal", "high"]

# Highest priority first; also the order the inbox lists reasons in.
REASONS: tuple[Reason, ...] = (
    "urgent",
    "human_requested",
    "complaint",
    "change_request",
    "booking_unresolved",
    "integration_failing",
    "clarification_exhausted",
)

# Bounds for the owner's settings (`escalation_settings`, CHECKs match).
MIN_CLARIFICATION_THRESHOLD = 1
MAX_CLARIFICATION_THRESHOLD = 5
DEFAULT_CLARIFICATION_THRESHOLD = 2
MAX_URGENT_KEYWORDS = 20
MAX_URGENT_KEYWORD_CHARS = 40


@dataclass(frozen=True)
class EscalationRules:
    """The owner's escalation settings that change the decision."""

    clarification_threshold: int = DEFAULT_CLARIFICATION_THRESHOLD
    # Lower-cased, stripped phrases; matched as whole words in the customer's message.
    urgent_keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class TurnSignals:
    """What happened in this turn, as the backend knows it."""

    # The customer's message (already redacted). Matched against keyword rules only;
    # it never leaves this function (not in the decision, not in logs).
    customer_text: str
    # The model's intent label for a shown reply; None when the turn ended in a fallback.
    intent: str | None
    model_requires_human: bool
    # The reply shown was one of the fixed fallback lines.
    fallback: bool


@dataclass(frozen=True)
class OperationFact:
    """One `interaction_operations` row of this interaction (oldest first)."""

    tool: str
    status: Literal["succeeded", "rejected", "failed"]
    # Machine result code from the op log (e.g. `booking_failed`, `calendar_unavailable`).
    result: str | None = None


@dataclass(frozen=True)
class EscalationDecision:
    reason: Reason
    urgency: Urgency
    # Every rule that fired, as fixed codes (e.g. "intent:COMPLAINT", "keyword:human"),
    # for the log and tests. Never customer text.
    signals: tuple[str, ...] = field(default_factory=tuple)


# The same integration failing this many times in one interaction is a pattern, not a blip.
REPEATED_FAILURES = 2
_LLM_SOURCE = "llm"
# Op-log results that mean "the integration behind this tool was down" (a failed booking
# has its own reason, `booking_unresolved`).
_UNAVAILABLE_RESULTS = frozenset({"calendar_unavailable", "tool_unavailable"})
_CHANGE_INTENTS = frozenset({"RESCHEDULE", "CANCEL"})


# --- Phrase tables ---------------------------------------------------------------
#
# Matched against the customer's lower-cased message with curly apostrophes folded to
# "'". Conservative on purpose: a miss still has the model's intent, a false positive
# texts the owner. `_NEGATED_BEFORE` drops a hit that follows "don't need", "no", "not
# an", "rather not" in the same clause.

_NEGATED_BEFORE = re.compile(
    r"(?:\b(?:no|not)\s+need\s+(?:for\s+|to\s+)?(?:a\s+|an\s+|the\s+|any\s+)?"
    r"|\b(?:don't|do\s+not|doesn't|didn't|never|no\s+longer)\s+(?:need|want|require|wish|have)"
    r"\s+(?:to\s+)?(?:a\s+|an\s+|the\s+|any\s+)?"
    r"|\brather\s+not\s+(?:to\s+)?(?:a\s+|an\s+|the\s+)?"
    r"|\b(?:no|without|zero)\s+(?:real\s+|any\s+)?"
    r"|\bnever\s*mind\s+(?:the\s+|a\s+)?"
    r"|\b(?:not|isn't|wasn't|aren't)\s+(?:really\s+)?(?:an?\s+)?)$"
)
_CLAUSE_END = re.compile(r"[.!?;,:\n]")

_SAFETY_PHRASES = (
    r"(?:this|it)(?:'s|\s+is)\s+an?\s+(?:real\s+|medical\s+)?emergency",
    r"(?:medical|real)\s+emergency",
    r"(?:i|we)\s+(?:have|need)\s+an?\s+emergency",
    r"(?:can't|cannot|can\s+not|couldn't|unable\s+to)\s+breathe",
    r"(?:trouble|difficulty|struggling)\s+(?:to\s+)?breath(?:e|ing)",
    r"(?:not|stopped)\s+breathing",
    r"chest\s+pains?",
    r"heart\s+attack",
    r"having\s+a\s+stroke",
    r"bleeding\s+(?:heavily|badly|profusely|a\s+lot|out)",
    r"won't\s+stop\s+bleeding",
    r"unconscious",
    r"unresponsive",
    r"passed\s+out",
    r"allergic\s+reaction",
    r"anaphyla\w+",
    r"overdos(?:e|ed|ing)",
    r"suicid(?:e|al)",
    r"kill\s+myself",
    r"(?:hurt|harm)\s+myself",
    r"end\s+(?:my\s+life|it\s+all)",
    r"want\s+to\s+die",
    r"(?:there's|there\s+is)\s+a\s+fire",
    r"on\s+fire",
    r"caught\s+fire",
    r"(?:smell|smelling)\s+(?:gas|smoke)",
    r"gas\s+leak",
    r"call\s+(?:an\s+ambulance|911|999|the\s+police|the\s+fire\s+department)",
    r"need\s+an\s+ambulance",
    r"someone\s+(?:is|got)\s+(?:badly\s+)?(?:hurt|injured)",
    r"being\s+(?:attacked|assaulted)",
)
_HUMAN_PHRASES = (
    r"(?:real|actual|live)\s+(?:person|human|people)",
    r"(?:want|need|get|prefer|ask\s+for|is\s+there)\s+(?:a|an)\s+human",
    r"(?:a|an)\s+human\s+(?:please|being|agent|representative)",
    r"(?:get|give)\s+me\s+(?:the\s+|your\s+)?(?:owner|manager|supervisor)",
    r"(?:speak|talk|chat)\s+(?:to|with)\s+(?:a\s+|an\s+|the\s+|your\s+)?"
    r"(?:real\s+|live\s+|actual\s+)?(?:human|person|people|someone|somebody|manager|owner|"
    r"supervisor|representative|rep|agent|operator|staff|receptionist)",
    r"(?:want|need|get|see|ask\s+for|call|put\s+on|speak\s+to|talk\s+to)\s+"
    r"(?:the|your|a)\s+(?:manager|owner|supervisor)",
    r"(?:transfer|connect)\s+me",
    r"put\s+me\s+through",
    r"(?:representative|operator)",
    r"(?:have|get)\s+(?:the\s+|an?\s+)?(?:owner|manager|someone|somebody)\s+"
    r"(?:to\s+)?(?:call|phone|ring|contact)\s+me",
    r"(?:call|phone|ring)\s+me\s+back",
)
_COMPLAINT_PHRASES = (
    r"complain(?:t|ts|ed|ing)?",
    r"(?:want|need|get|give\s+me|demand|ask\s+for|like)\s+(?:a\s+|my\s+|the\s+)?"
    r"(?:refund|money\s+back)",
    r"money\s+back",
    r"came\s+out\s+(?:wrong|bad|badly|terrible|terribly|awful|horrible|uneven|crooked)",
    r"ruined",
    r"(?:very|really|so|extremely|totally|completely)\s+(?:unhappy|upset|disappointed|"
    r"dissatisfied|angry)",
    r"not\s+(?:happy|satisfied|pleased)\s+with",
    r"(?:terrible|awful|horrible|appalling|poor)\s+(?:service|experience|work)",
    r"unacceptable",
    r"rip(?:ped)?[\s-]?off",
)


def _fold(text: str) -> str:
    """Lower-cased, curly apostrophes straight, whitespace collapsed (Unicode safe)."""
    folded = text.replace("’", "'").replace("‘", "'").replace("ʼ", "'")
    return " ".join(folded.casefold().split())


def _compile(phrases: Sequence[str]) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)(?:" + "|".join(phrases) + r")(?!\w)")


_SAFETY = _compile(_SAFETY_PHRASES)
_HUMAN = _compile(_HUMAN_PHRASES)
_COMPLAINT = _compile(_COMPLAINT_PHRASES)


def _matches(pattern: re.Pattern[str], text: str) -> bool:
    """`text` (already folded) has a hit of `pattern` that isn't negated in its clause."""
    for match in pattern.finditer(text):
        window = text[max(0, match.start() - 40) : match.start()]
        ends = list(_CLAUSE_END.finditer(window))
        clause = window[ends[-1].end() :] if ends else window
        if not _NEGATED_BEFORE.search(clause):
            return True
    return False


@lru_cache(maxsize=256)
def _keyword_pattern(keywords: tuple[str, ...]) -> re.Pattern[str] | None:
    words = [_fold(word) for word in keywords]
    escaped = [r"\s+".join(re.escape(part) for part in word.split()) for word in words if word]
    return _compile(escaped) if escaped else None


def evaluate(
    state: Mapping[str, Any],
    turn: TurnSignals,
    operations: Sequence[OperationFact],
    rules: EscalationRules,
) -> EscalationDecision | None:
    """`state` is `interactions.state` AFTER this turn was merged (clarification_attempts,
    failures, requires_human). Returns the highest-priority reason, or None."""
    text = _fold(turn.customer_text)
    fired: dict[Reason, list[str]] = {}

    def fire(reason: Reason, signal: str) -> None:
        fired.setdefault(reason, []).append(signal)

    if turn.intent == "URGENT":
        fire("urgent", "intent:URGENT")
    if _matches(_SAFETY, text):
        fire("urgent", "safety_phrase")
    keywords = _keyword_pattern(tuple(rules.urgent_keywords))
    if keywords is not None and _matches(keywords, text):
        fire("urgent", "owner_keyword")

    if turn.intent == "HUMAN_REQUEST":
        fire("human_requested", "intent:HUMAN_REQUEST")
    if _matches(_HUMAN, text):
        fire("human_requested", "human_phrase")

    if turn.intent == "COMPLAINT":
        fire("complaint", "intent:COMPLAINT")
    if _matches(_COMPLAINT, text):
        fire("complaint", "complaint_phrase")

    if turn.intent in _CHANGE_INTENTS:
        fire("change_request", f"intent:{turn.intent}")

    if any(op.tool == "create_booking" and op.result == "booking_failed" for op in operations):
        fire("booking_unresolved", "booking_failed")

    for source, count in sorted(_failure_counts(state, operations).items()):
        if count >= REPEATED_FAILURES:
            fire("integration_failing", f"repeated_failure:{source}")

    if int(state.get("clarification_attempts", 0) or 0) >= rules.clarification_threshold:
        fire("clarification_exhausted", "clarification_attempts")

    for reason in REASONS:
        if reason in fired:
            signals = [signal for r in REASONS for signal in fired.get(r, [])]
            if turn.model_requires_human:
                signals.append("model_flag")
            return EscalationDecision(
                reason=reason,
                urgency="high" if reason == "urgent" else "normal",
                signals=tuple(signals),
            )
    return None


def _failure_counts(state: Mapping[str, Any], operations: Sequence[OperationFact]) -> Counter[str]:
    """Failures per integration across the whole interaction: the tool name for the op
    log, `llm` for the model provider."""
    counts: Counter[str] = Counter()
    for op in operations:
        if op.result == "booking_failed":
            continue
        if op.status == "failed" or op.result in _UNAVAILABLE_RESULTS:
            counts[op.tool] += 1
    for failure in state.get("failures") or ():
        if isinstance(failure, Mapping) and failure.get("kind") == "provider_unavailable":
            counts[_LLM_SOURCE] += 1
    return counts


EMERGENCY_REPLY = (
    "If anyone is in danger or this is a medical emergency, please contact your local "
    "emergency services right away. I can't give medical advice, but I've passed this on "
    "to the team."
)


_EMERGENCY_POINTER = re.compile(
    r"emergency\s+(?:services|number|room|department|dispatch|hotline|line|help)"
    r"|(?:call|dial|contact|phone)\s+(?:911|999|112|000|the\s+police|an\s+ambulance|"
    r"(?:your\s+)?(?:local\s+)?emergency)"
    r"|(?<!\d)(?:911|999|112)(?!\d)|\bambulance\b|\bcrisis\s+(?:line|hotline)\b",
    re.IGNORECASE,
)


def needs_emergency_pointer(decision: EscalationDecision | None, reply_text: str) -> bool:
    """True when an urgent decision's reply doesn't point the customer to emergency
    services, so the engine must replace it with `EMERGENCY_REPLY`. Only for a
    safety-origin urgency (the model's URGENT intent or a safety phrase): an urgent
    decision that fired only on the owner's own keyword is alerted but keeps the reply."""
    if decision is None or decision.reason != "urgent":
        return False
    if not {"intent:URGENT", "safety_phrase"} & set(decision.signals):
        return False
    return _EMERGENCY_POINTER.search(reply_text) is None
