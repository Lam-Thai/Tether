"""Grounding guard: is a final reply backed by what the backend verified in this turn?

`grounding_problem(turn, evidence)` returns a fixed reason code (or None), never
text from the reply. `TurnEvidence` collects, for one turn only, what the reply
may rely on; earlier turns never ground a later one (the owner may have edited
or disabled the fact since).

- Sources. Every id in `sources` (whatever the intent) must be valid this turn:
  an FAQ id `search_faq` returned, a service id `get_services` returned,
  `business_hours` (only when the business has listed hours and they reached
  the model: the prompt's BUSINESS HOURS or a `get_business_info` result), or
  `business_profile` (always: BUSINESS FACTS is in every prompt).
- Intents. A reply with an intent in `GROUNDED_INTENTS` must cite at least one
  source of a kind `ACCEPTED_SOURCES` allows for it (a price needs a service
  or an FAQ, not the address). One exemption: a `PRICE_INQUIRY` may cite
  nothing when a lookup this turn verified there is no single answer
  (`get_services` came back `not_found` or `ambiguous`), which is the honest
  "we don't list that" / "which one did you mean?".
- Amounts, every intent. Each money amount and each duration in the reply
  must equal a price or duration of a service retrieved this turn, or one
  stated in an FAQ answer retrieved this turn. Compared as numbers: $40 is
  40.00, 90 minutes is 1.5 hours.

Amount detection is deliberately narrow, so clock times ("9:00", "10 AM"),
dates, phone numbers, street numbers and ages are never amounts: money is a
number next to a currency symbol, an ISO code or a currency word; a duration
is a number followed by a minute or hour unit, plus a few phrases ("an hour",
"half an hour"). Spelled-out numbers up to the hundreds are read too, since
voice replies say "forty dollars". Anything else (a "free" consultation, a
percentage) is left to the prompt rules: the guard checks ids and numbers,
not wording.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Literal

from schemas.agent_turn import AgentTurn
from services.knowledge import FaqSnippet

SourceKind = Literal["faq", "service", "business_hours", "business_profile"]

# The two named sources (everything else cited is an FAQ or service id).
BUSINESS_PROFILE = "business_profile"
BUSINESS_HOURS = "business_hours"

# Intents that state business facts, and the source kinds that can back each.
ACCEPTED_SOURCES: Mapping[str, frozenset[SourceKind]] = {
    "FAQ": frozenset({"faq"}),
    "BUSINESS_HOURS": frozenset({"business_hours", "faq"}),
    "LOCATION": frozenset({"business_profile", "faq"}),
    "PRICE_INQUIRY": frozenset({"service", "faq"}),
}
GROUNDED_INTENTS = frozenset(ACCEPTED_SOURCES)
_NO_SERVICE_DURATIONS = frozenset({"LOCATION", "BUSINESS_HOURS"})


@dataclass(frozen=True)
class Evidence:
    """What one verified tool result lets this turn's reply cite and quote."""

    # Citable ids (lowercase) and what each one is.
    sources: Mapping[str, SourceKind] = field(default_factory=dict)
    # Service prices (in the business currency) and durations, in minutes.
    prices: tuple[Decimal, ...] = ()
    durations: tuple[int, ...] = ()
    # Intents this result verified have no single answer, so the reply may cite
    # nothing (a service lookup that found nothing, or several candidates).
    unresolved: frozenset[str] = frozenset()


class TurnEvidence:
    """Everything one turn's reply may rely on. Built fresh for every turn."""

    def __init__(self, *, hours_listed: bool) -> None:
        self._sources: dict[str, SourceKind] = {BUSINESS_PROFILE: "business_profile"}
        if hours_listed:
            self._sources[BUSINESS_HOURS] = "business_hours"
        self._faq_answers: list[str] = []
        self._prices: set[Decimal] = set()
        self._durations: set[Decimal] = set()
        self._unresolved: set[str] = set()

    def add_faq(self, snippet: FaqSnippet) -> None:
        self._sources[str(snippet.id).lower()] = "faq"
        self._faq_answers.append(snippet.answer)

    def add(self, evidence: Evidence) -> None:
        for source, kind in evidence.sources.items():
            self._sources[source.strip().lower()] = kind
        self._prices.update(evidence.prices)
        self._durations.update(Decimal(minutes) for minutes in evidence.durations)
        self._unresolved.update(evidence.unresolved)

    def kind_of(self, source: str) -> SourceKind | None:
        return self._sources.get(source.strip().lower())

    def problem(self, turn: AgentTurn) -> str | None:
        cited = {source.strip().lower() for source in turn.sources}
        kinds = {self._sources.get(source) for source in cited}
        if None in kinds:
            return "unknown_source"
        accepted = ACCEPTED_SOURCES.get(turn.intent)
        # The "no single answer" exemption ends once any service was found this
        # turn: then there is a real price the reply should cite instead.
        exempt = turn.intent in self._unresolved and not (self._prices or self._durations)
        if accepted is not None and not kinds & accepted and not exempt:
            return "unsupported_source" if cited else "missing_sources"
        return self._amount_problem(turn.response_text, turn.intent)

    def _amount_problem(self, text: str, intent: str) -> str | None:
        money = money_amounts(text)
        if money and not set(money) <= self._prices | _all(money_amounts, self._faq_answers):
            return "unverified_amount"
        # A location or hours reply's durations are travel or notice times ("5 minutes
        # from the station", "call 24 hours ahead"), not service lengths; those
        # replies are held to their cited source instead. Money is checked always.
        minutes = () if intent in _NO_SERVICE_DURATIONS else duration_minutes(text)
        if minutes and not set(minutes) <= self._durations | _all(
            duration_minutes, self._faq_answers
        ):
            return "unverified_duration"
        return None


def grounding_problem(turn: AgentTurn, evidence: TurnEvidence) -> str | None:
    """Why `turn` isn't grounded in this turn's evidence, as a fixed code (or None)."""
    return evidence.problem(turn)


def _all(extract, texts: Iterable[str]) -> set[Decimal]:
    return {value for text in texts for value in extract(text)}


# --- Amount detection ----------------------------------------------------------

_UNITS = (
    "one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
_WORD_VALUES = {word: n for n, word in enumerate(_UNITS, start=1)} | {
    word: 10 * n for n, word in enumerate(_TENS, start=2)
}


def _alternation(words: Iterable[str]) -> str:
    # Longest first, so "seventeen" is never cut short as "seven".
    return "|".join(sorted(words, key=len, reverse=True))


_ONE_TO_NINE = _alternation(_UNITS[:9])
# 1..99 in words ("forty-five"), optionally after "<n> hundred (and)".
_SMALL = rf"(?:(?:{_alternation(_TENS)})(?:[-\s](?:{_ONE_TO_NINE}))?|{_alternation(_UNITS)})"
_WORD_NUMBER = rf"(?i:\b(?:(?:a|{_ONE_TO_NINE})\s+hundred(?:\s+(?:and\s+)?{_SMALL})?|{_SMALL})\b)"
# Digits with optional thousands separators and up to two decimals ("1,500.50", "40,00").
# Not after a lowercase letter, digit or separator ("x40", "1.5.40"); an uppercase
# letter is allowed so an ISO code glued to its amount ("EUR40") still reads.
_DIGITS = r"(?<![\d.,_a-z])\d+(?:[.,]\d{3})*(?:[.,]\d{1,2})?(?![\d])"
_NUMBER = rf"(?:{_DIGITS}|{_WORD_NUMBER})"
# "40-50", "40 to 50"; a duration may also be "15 or 20 minutes".
_RANGE = r"\s?(?:-|–|—|(?i:to))\s?"
_DURATION_RANGE = r"\s?(?:-|–|—|(?i:to|or))\s?"

# Case-sensitive on purpose: an uppercase ISO code, never a word like "try".
_CODES = (
    "USD|CAD|EUR|GBP|AUD|NZD|CHF|SEK|NOK|DKK|SGD|HKD|INR|MXN|BRL|ZAR|PHP|THB|CNY|JPY|KRW|VND"
    "|IDR|MYR|PLN|CZK|HUF|TRY|ILS|AED|SAR|TWD|ARS|CLP|COP|PEN|NGN|KES|EGP|RUB|UAH|ISK|RON"
)
# "$" with an optional country prefix (US$, CA$, A$, NZ$, HK$, S$, R$), and other symbols.
_SYMBOL = r"(?:[A-Z]{1,2})?\$|€|£|¥|₫|₹|₩|₪|₱|₺|₽|฿|₦|₴|₡|₲|₵|₸|₼|₾"
_MONEY_WORDS = (
    r"(?i:dollars?|bucks|euros?|pounds?|quid|yen|yuan|renminbi|dong|pesos?|rupees?|rand|baht"
    r"|won|francs?|kronor|krona|kroner|krone|reais|ringgit|zloty|zlotys|shekels?|dirhams?)"
)
_SUBUNIT_WORDS = r"(?i:cents?|pence|centavos?|centimes?)"
_AMOUNT_RANGE = rf"(?P<a>{_NUMBER})(?:{_RANGE}(?P<b>{_NUMBER}))?"

_MONEY_PATTERNS: tuple[tuple[re.Pattern[str], Decimal], ...] = (
    # "$40", "CA$ 40.00", "€40-€50"
    (
        re.compile(
            rf"(?:{_SYMBOL})\s?(?P<a>{_NUMBER})(?:{_RANGE}(?:{_SYMBOL})?\s?(?P<b>{_NUMBER}))?"
        ),
        Decimal(1),
    ),
    # "USD 40", "EUR 40 to 50"
    (re.compile(rf"\b(?:{_CODES})\s?{_AMOUNT_RANGE}"), Decimal(1)),
    # "50 cents" (before the word forms, so it isn't read as 50 of the main unit)
    (re.compile(rf"{_AMOUNT_RANGE}\s?{_SUBUNIT_WORDS}\b"), Decimal("0.01")),
    # "40 €", "40 USD", "forty dollars", "40 to 50 dollars"
    (
        re.compile(rf"{_AMOUNT_RANGE}\s?(?:(?:{_SYMBOL})|\b(?:{_CODES})\b|{_MONEY_WORDS}\b)"),
        Decimal(1),
    ),
)

_HOURS = r"(?i:hours?|hrs?)"
_MINUTES = r"(?i:minutes?|mins?)"
_DURATION = rf"(?P<a>{_NUMBER})(?:{_DURATION_RANGE}(?P<b>{_NUMBER}))?\s?-?\s?"
# Order matters: compound and phrase forms first, so "1 hour 30 minutes" is one
# duration (90), not "1 hour" and "30 minutes". Each match is masked before the next.
_DURATION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            rf"(?P<a>{_NUMBER})\s?-?\s?{_HOURS}\s?,?\s?(?i:and\s+)?"
            rf"(?P<b>{_NUMBER})\s?-?\s?{_MINUTES}\b"
        ),
        "hours_and_minutes",
    ),
    (re.compile(rf"(?P<a>{_NUMBER})\s+(?i:and\s+a\s+half)\s+{_HOURS}\b"), "hours_and_a_half"),
    (re.compile(r"(?i:\b(?:an?|one)\s+hour\s+and\s+a\s+half\b)"), "90"),
    (re.compile(r"(?i:\bhalf\s+an\s+hour\b|\b(?:a\s+)?half[-\s]hour\b)"), "30"),
    (re.compile(r"(?i:\b(?:a\s+)?quarter\s+(?:of\s+)?an\s+hour\b|\bquarter[-\s]hour\b)"), "15"),
    (re.compile(r"(?i:\ban\s+hour\b)"), "60"),
    (re.compile(rf"{_DURATION}{_HOURS}\b"), "hours"),
    (re.compile(rf"{_DURATION}{_MINUTES}\b"), "minutes"),
)


def money_amounts(text: str) -> list[Decimal]:
    """Every money amount in `text`, as a number in its main unit (50 cents is 0.50)."""
    found: list[Decimal] = []
    for pattern, scale in _MONEY_PATTERNS:
        text = _consume(pattern, text, lambda m, scale=scale: _range(m, scale), found)
    return found


def duration_minutes(text: str) -> list[Decimal]:
    """Every duration in `text`, in minutes."""
    found: list[Decimal] = []
    for pattern, form in _DURATION_PATTERNS:
        text = _consume(pattern, text, lambda m, form=form: _duration(m, form), found)
    return found


def _consume(pattern: re.Pattern[str], text: str, read, found: list[Decimal]) -> str:
    """Appends what `read` makes of each match; returns `text` with the matches blanked
    out (same length), so a later, looser pattern can't read them a second time."""
    for match in pattern.finditer(text):
        found.extend(read(match))
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


def _range(match: re.Match[str], scale: Decimal) -> list[Decimal]:
    values = [_number(match.group("a"))]
    if match.group("b"):
        values.append(_number(match.group("b")))
    return [value * scale for value in values]


def _duration(match: re.Match[str], form: str) -> list[Decimal]:
    if form == "hours_and_minutes":
        return [_number(match.group("a")) * 60 + _number(match.group("b"))]
    if form == "hours_and_a_half":
        return [_number(match.group("a")) * 60 + 30]
    if form in ("hours", "minutes"):
        return _range(match, Decimal(60) if form == "hours" else Decimal(1))
    return [Decimal(form)]


def _number(text: str) -> Decimal:
    """A matched number as a Decimal: the last separator followed by one or two digits
    is the decimal point ("40.00", "40,5"); every other separator groups thousands."""
    text = text.strip()
    if not text[0].isdigit():
        return Decimal(_word_value(text))
    separators = [i for i, char in enumerate(text) if char in ".,"]
    if separators and len(text) - separators[-1] - 1 in (1, 2):
        whole = re.sub(r"[.,]", "", text[: separators[-1]])
        text = f"{whole}.{text[separators[-1] + 1 :]}"
    else:
        text = re.sub(r"[.,]", "", text)
    try:
        return Decimal(text)
    except InvalidOperation:  # pragma: no cover - the patterns only match digits
        return Decimal(-1)


def _word_value(text: str) -> int:
    """'forty-five' -> 45, 'a hundred and twenty' -> 120 (what `_WORD_NUMBER` matches)."""
    total = 0
    for word in re.split(r"[-\s]+", text.lower()):
        if word == "hundred":
            total = max(total, 1) * 100
        elif word in _WORD_VALUES:
            total += _WORD_VALUES[word]
    return total
