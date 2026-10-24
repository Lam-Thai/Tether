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

- Clock times, every intent. Each time of day in the reply ("2:30 PM", "14:30",
  "2pm", "noon", "two thirty", "half past two", "at 2", "9-5") must be a start
  time of the LAST slot offer `check_availability` made this turn (a later offer
  replaces an earlier one, so a time is only ever valid for the date and service
  it was offered for). With no offer this turn, a reply may repeat a start time of
  the offer the customer heard last turn (`earlier_offer_times`, the saved
  `offered_slots`) unless a check this turn found nothing to offer, and an hours,
  FAQ or location reply (`HOURS_TIME_INTENTS`) that doesn't use offer wording
  ("available", "book", "works", ...) may state opening or closing times shown
  this turn or times in an FAQ answer retrieved this turn. Intent is the model's
  word, so an hours reply that offers a time is held to the slots like a booking
  one. A time without am/pm counts if either
  reading is allowed. A miss is `unverified_time`, which the engine answers with
  one regeneration and then the availability fallback.

- Booking claims, every intent. A reply that says an appointment was booked ("you're
  all set", "I've booked you in", "your haircut is confirmed") is allowed only when
  `create_booking` returned `confirmed` this turn (`Evidence.booking_confirmed`). A
  claim that something was cancelled, rescheduled or moved is never allowed: no tool
  does that yet (slice 13 escalates those requests). A miss is `unconfirmed_booking`,
  which the engine answers with one regeneration and then the booking fallback.
  Questions ("shall I book it?") and conditionals ("once you're booked") are not
  claims, and neither is "that time is already booked" (someone else's booking).

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
from datetime import time
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
# Replies that offer or move an appointment: they may name only offered slot times.
BOOKING_INTENTS = frozenset({"BOOK_APPOINTMENT", "RESCHEDULE"})
# Replies that may quote opening/closing and FAQ times (when they don't offer a time).
HOURS_TIME_INTENTS = frozenset({"BUSINESS_HOURS", "FAQ", "LOCATION"})
UNVERIFIED_TIME = "unverified_time"
UNCONFIRMED_BOOKING = "unconfirmed_booking"
# Wording that offers, accepts or proposes a time: such a reply is held to the slots.
_OFFER_WORDING = re.compile(
    r"(?i:\b(?:availab\w*|book\w*|appointments?|slots?|reserv\w*|works?|"
    r"see\s+you|i\s+have|i\s+can|how\s+about|fit\s+you|squeeze)\b)"
)
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
    # Local start times of the slots offered to the customer (`check_availability`).
    # Non-empty means "this is an offer": it replaces any earlier offer this turn.
    slot_times: frozenset[time] = frozenset()
    # A fresh availability check found nothing to offer: last turn's offer is stale
    # and may no longer be repeated.
    retires_earlier_offer: bool = False
    # Local opening and closing times of hours shown to the model.
    hour_times: frozenset[time] = frozenset()
    # `create_booking` returned `confirmed`: the reply may say the appointment is booked.
    booking_confirmed: bool = False


class TurnEvidence:
    """Everything one turn's reply may rely on. Built fresh for every turn."""

    def __init__(
        self,
        *,
        hours_listed: bool,
        hour_times: Iterable[time] = (),
        earlier_offer_times: Iterable[time] = (),
    ) -> None:
        """`hour_times`: opening and closing times of the hours already in the prompt.
        `earlier_offer_times`: start times of the offer the customer heard last turn."""
        self._sources: dict[str, SourceKind] = {BUSINESS_PROFILE: "business_profile"}
        if hours_listed:
            self._sources[BUSINESS_HOURS] = "business_hours"
        self._faq_answers: list[str] = []
        self._prices: set[Decimal] = set()
        self._durations: set[Decimal] = set()
        self._unresolved: set[str] = set()
        self._slot_times: set[time] = set()
        self._hour_times: set[time] = set(hour_times)
        self._earlier_offer_times: set[time] = set(earlier_offer_times)
        self._booking_confirmed = False

    def add_faq(self, snippet: FaqSnippet) -> None:
        self._sources[str(snippet.id).lower()] = "faq"
        self._faq_answers.append(snippet.answer)

    def add(self, evidence: Evidence) -> None:
        for source, kind in evidence.sources.items():
            self._sources[source.strip().lower()] = kind
        self._prices.update(evidence.prices)
        self._durations.update(Decimal(minutes) for minutes in evidence.durations)
        self._unresolved.update(evidence.unresolved)
        if evidence.slot_times:
            self._slot_times = set(evidence.slot_times)  # the latest offer wins
        if evidence.retires_earlier_offer:
            self._earlier_offer_times.clear()
        self._hour_times.update(evidence.hour_times)
        self._booking_confirmed = self._booking_confirmed or evidence.booking_confirmed

    def kind_of(self, source: str) -> SourceKind | None:
        return self._sources.get(source.strip().lower())

    def problem(self, turn: AgentTurn) -> str | None:
        for claim in booking_claims(turn.response_text):
            if claim == "change" or not self._booking_confirmed:
                return UNCONFIRMED_BOOKING
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
        return self._amount_problem(turn.response_text, turn.intent) or self._time_problem(turn)

    def _time_problem(self, turn: AgentTurn) -> str | None:
        mentioned = clock_times(turn.response_text)
        if not mentioned:
            return None
        if self._slot_times:
            allowed = set(self._slot_times)
        else:
            allowed = set(self._earlier_offer_times)
            if turn.intent in HOURS_TIME_INTENTS and not _OFFER_WORDING.search(turn.response_text):
                allowed |= self._hour_times
                allowed |= {
                    reading
                    for answer in self._faq_answers
                    for readings in clock_times(answer)
                    for reading in readings
                }
        if any(not readings & allowed for readings in mentioned):
            return UNVERIFIED_TIME
        return None

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


# --- Booking-claim detection ---------------------------------------------------

ClaimKind = Literal["booking", "change"]

# Adverbs between the subject and the verb ("you're now all set", "I've gone ahead and
# booked"). Not "not": "you're not booked yet" is no claim.
_CLAIM_ADVERBS = r"(?:\s+(?:now|just|already|all|officially|successfully|gone\s+ahead\s+and))*"
_BOOKED = r"(?:all\s+set|booked|confirmed|scheduled|reserved|locked\s+in|pencill?ed\s+in|set\s+up)"
_CHANGED = r"(?:cancell?ed|rescheduled|moved)"
# Conditionals are not claims ("once you're booked", "if you're all set"). Not "so": "So
# you're booked for Friday" IS a claim.
_NOT_AFTER = "".join(
    rf"(?<!\b{word}\s)" for word in ("once", "when", "if", "until", "after", "before")
)
_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "you're booked", "you are all set", "you've been cancelled", "you're booked with Maria"
    re.compile(
        rf"{_NOT_AFTER}\byou(?:['’]re|\s+are|['’]ve\s+been|\s+have\s+been)"
        rf"{_CLAIM_ADVERBS}\s+(?P<verb>{_BOOKED}|{_CHANGED})\b"
        # Only for "all set" / "confirmed": "you're all set with that question" and
        # "you're confirmed as the contact" are no claims.
        r"(?!(?<=\bset)\s+(?:with|as)\b)(?!(?<=\bconfirmed)\s+(?:with|as)\b)",
        re.IGNORECASE,
    ),
    # "I've booked you", "we have scheduled your haircut", "I've cancelled it", "I've booked
    # a haircut for you". The object is the customer or an appointment, so "I've confirmed
    # that we're open" or "I've confirmed it with the team" is no claim.
    re.compile(
        rf"{_NOT_AFTER}\b(?:i|we)(?:['’]ve|\s+have){_CLAIM_ADVERBS}\s+"
        rf"(?P<verb>booked|scheduled|confirmed|reserved|{_CHANGED})\s+"
        r"(?:you\b|your\b|it\b(?!\s+with\b)"
        r"|(?:the|an?)\s+(?:[\w-]+\s+){0,2}?"
        r"(?:appointment|booking|reservation|slot|spot|visit|session)s?\b"
        r"|(?:the|an?)\s+(?:[\w-]+\s+){1,3}?for\s+you\b)",
        re.IGNORECASE,
    ),
    # "your appointment is confirmed", "your Friday haircut has been booked"
    re.compile(
        rf"{_NOT_AFTER}\byour\s+(?:[\w'’-]+\s+){{1,4}}?(?:is|has\s+been|was|['’]s)"
        rf"{_CLAIM_ADVERBS}\s+(?P<verb>{_BOOKED}|{_CHANGED})\b",
        re.IGNORECASE,
    ),
    # "I've got you down for 3:30", "I have you in"
    re.compile(
        rf"{_NOT_AFTER}\bi(?:['’]ve|\s+have)\s+(?:got\s+)?you\s+(?P<verb>down|in|booked|scheduled)\b",
        re.IGNORECASE,
    ),
    # "That's confirmed", "it is all set" (not "booked": "it's booked" is a taken slot)
    re.compile(
        rf"{_NOT_AFTER}\b(?:it|that|this)(?:['’]s|\s+is|\s+has\s+been)(?:\s+(?:now|all))?"
        r"\s+(?P<verb>confirmed|all\s+set|locked\s+in)\b",
        re.IGNORECASE,
    ),
    # A sentence that opens with the claim: "Booked!", "Confirmed for Friday at 3:30."
    re.compile(
        r"^\s*(?P<verb>all\s+set|booked|confirmed|cancell?ed|rescheduled)\b(?=\s*(?:[!.,]|$|for\b))",
        re.IGNORECASE,
    ),
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
# A dash or semicolon starts a new clause: "You're all set - anything else?"
_CLAUSE_BREAK = re.compile(r"\s*[—–;]\s*|\s+-\s+")
# A clause that asks: it ends with "?" AND opens like a question ("Shall I have you down
# for 3:30?"). "You're booked, anything else?" ends with "?" but states a booking.
_QUESTION = re.compile(
    r"^\W*(?:(?:so|ok(?:ay)?|great|alright|and|then|perfect)\W+)*"
    r"(?:shall|should|can|could|would|will|do|does|did|want|may|might|is|are|am|was|"
    r"were|have|has|how|what|which|when|where|who|why)\b.*\?\s*$",
    re.IGNORECASE | re.DOTALL,
)


def booking_claims(text: str) -> list[ClaimKind]:
    """Every statement in `text` that an appointment was booked (`booking`) or cancelled,
    rescheduled or moved (`change`). A clause that asks a question doesn't count, but a
    claim followed by a question ("You're booked, anything else?") does."""
    claims: list[ClaimKind] = []
    for sentence in _SENTENCE_END.split(text):
        for clause in _CLAUSE_BREAK.split(sentence):
            if not clause.strip() or _QUESTION.match(clause):
                continue
            for pattern in _CLAIM_PATTERNS:
                for match in pattern.finditer(clause):
                    verb = match.group("verb").lower()
                    changed = re.fullmatch(_CHANGED, verb, re.IGNORECASE) is not None
                    claims.append("change" if changed else "booking")
    return claims


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


# --- Clock-time detection ------------------------------------------------------
#
# Strict on purpose: a time this misses is a time the guard can't check, so new
# phrasings are added here, not left to the prompt. A false positive only costs a
# regeneration (or the availability fallback).

_HOUR_WORDS = {word: n for n, word in enumerate(_UNITS[:12], start=1)}
_HOUR_WORD = rf"(?:{_alternation(_HOUR_WORDS)})"
_HOUR_DIGITS = r"(?:1[0-2]|0?[1-9])"
_HOUR = rf"(?:{_HOUR_DIGITS}|{_HOUR_WORD})"
_MERIDIEM = r"(?P<ap>[ap])\.?\s?m\b\.?"
_OCLOCK = r"o['’]?\s?clock\b"
_MINUTE_WORD = rf"(?:oh[\s-](?:{_ONE_TO_NINE})|{_SMALL})"
_NOT_A_TIME_AFTER = (
    r"(?:%|percent|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?"
    r"|people|persons?|guests?|dollars?|bucks|euros?|pounds?|spots?|slots?|openings?)"
)
_RANGE_JOIN = r"\s*(?:-|–|—|to|until|till|and|or)\s*"
# Words that make a bare "9" in "9 to 5" a time.
_RANGE_LEAD = r"(?:from|between|open|opens|opening|hours?|available|free|at)"


def _end(name: str) -> str:
    """One end of a range: an hour, optional :mm, optional am/pm (own group names)."""
    return (
        rf"(?P<{name}>{_HOUR})(?::(?P<{name}m>[0-5]\d))?"
        rf"(?:\s?(?P<{name}ap>[ap])\.?\s?m\b\.?)?"
    )


# Order matters, like the amounts: specific forms first, each match masked before the next.
_TIME_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # "from 9 to 5", "open 9-5", "between 2 and 4:30", "2-4 pm" (both ends are times)
    (
        re.compile(
            rf"(?i:\b{_RANGE_LEAD}\s+{_end('h')}{_RANGE_JOIN}{_end('k')}\b"
            rf"(?![:.,]?\d)(?!\s?{_NOT_A_TIME_AFTER}\b))"
        ),
        "range",
    ),
    (
        re.compile(
            rf"(?i:(?<![\d:.,])\b(?P<h>{_HOUR_DIGITS})(?::(?P<hm>[0-5]\d))?{_RANGE_JOIN}"
            rf"(?P<k>{_HOUR_DIGITS})(?::(?P<km>[0-5]\d))?\s?(?P<kap>[ap])\.?\s?m\b\.?)"
        ),
        "range",
    ),
    # "14:30", "2:30", "2:30 pm"
    (
        re.compile(
            rf"(?i:(?<![\d:])(?P<h>[01]?\d|2[0-3]):(?P<m>[0-5]\d)(?![\d:])(?:\s?{_MERIDIEM})?)"
        ),
        "digits",
    ),
    # "2.30pm", "2.30 p.m." (a dot only with am/pm, so prices and decimals never match)
    (
        re.compile(rf"(?i:(?<![\d.,$€£¥])\b(?P<h>{_HOUR_DIGITS})\.(?P<m>[0-5]\d)\s?{_MERIDIEM})"),
        "digits",
    ),
    # "14h30", "2h30"
    (re.compile(r"(?i:(?<![\d.,])\b(?P<h>[01]?\d|2[0-3])h(?P<m>[0-5]\d)\b)"), "digits"),
    # "half past 2", "quarter to three pm", "twenty past two", "10 minutes to 4",
    # "quarter of three"
    (
        re.compile(
            rf"(?i:\b(?P<q>half|(?:a\s+)?quarter|{_MINUTE_WORD}(?:\s+minutes?)?"
            rf"|[1-5]?\d\s+minutes?)\s+(?P<dir>past|after|to|till|before|of)\s+"
            rf"(?P<h>{_HOUR})\b(?:\s?{_MERIDIEM})?(?![\s-]?{_NOT_A_TIME_AFTER}\b))"
        ),
        "relative",
    ),
    # "half two" (British: 2:30)
    (re.compile(rf"(?i:\bhalf\s+(?P<h>{_HOUR})\b(?![\s-]?{_NOT_A_TIME_AFTER}\b))"), "half"),
    # "2pm", "2 p.m.", "2 o'clock"
    (re.compile(rf"(?i:(?<![\d:.,])\b(?P<h>{_HOUR_DIGITS})\s?(?:{_MERIDIEM}|{_OCLOCK}))"), "hour"),
    # "two thirty pm", "three o'clock", "four pm"
    (
        re.compile(
            rf"(?i:\b(?P<h>{_HOUR_WORD})(?:[\s-](?P<mw>{_MINUTE_WORD}))?\s?(?:{_MERIDIEM}|{_OCLOCK}))"
        ),
        "words",
    ),
    # "two thirty", "four fifteen": a voice reply's usual form, without am/pm
    (
        re.compile(
            rf"(?i:\b(?P<h>{_HOUR_WORD})[\s-](?P<mw>fifteen|thirty|forty[\s-]five"
            rf"|oh[\s-](?:{_ONE_TO_NINE})|ten|twenty|forty|fifty)\b(?![\s-]{_NOT_A_TIME_AFTER}\b))"
        ),
        "words",
    ),
    # "2 in the afternoon", "eight tonight", "9 this morning", "2ish"
    (
        re.compile(
            rf"(?i:\b(?P<h>{_HOUR})(?:\s?-?ish\b|\s+(?:in\s+the|this)\s+"
            rf"(?P<part>morning|afternoon|evening)\b|\s+(?P<night>tonight)\b))"
        ),
        "part",
    ),
    (re.compile(r"(?i:\b(?:noon|midday)\b)"), "noon"),
    (re.compile(r"(?i:\bmidnight\b)"), "midnight"),
    # "at 14", "at 1430": a 24-hour time after "at"
    (
        re.compile(
            r"(?i:\bat\s+(?P<h>1[3-9]|2[0-3])(?P<m>[0-5]\d)?\b"
            r"|\bat\s+(?P<h2>[01]\d)(?P<m2>[0-5]\d)\b)"
        ),
        "military",
    ),
    # "at 2", "around two", "by 3": a bare hour after "at" (or a time-ish preposition),
    # at the end of a phrase or before a word that follows a time ("at 2 on Friday"),
    # never an address ("at 1 Main St")
    (
        re.compile(
            rf"(?i:\b(?:at|around|by|before|after|until|till)\s+(?P<h>{_HOUR})\b(?![:.,]?\d)"
            r"(?=\s*(?:$|[.,;:!?)\-–—]|(?:on|or|and|today|tomorrow|tonight|this|next|then"
            r"|sharp|works|would|is|if|in|for|that|with|instead)\b)))"
        ),
        "hour",
    ),
)


def clock_times(text: str) -> list[frozenset[time]]:
    """Every time of day in `text`, each as its possible readings: one with am/pm or a
    24-hour value, two for a 12-hour time without am/pm ("2:30" is 02:30 or 14:30).
    A range ("9-5", "from 2 to 4 pm") is two times."""
    found: list[frozenset[time]] = []
    for pattern, form in _TIME_PATTERNS:
        for match in pattern.finditer(text):
            found.extend(readings for readings in _readings(match, form) if readings)
        text = pattern.sub(lambda m: " " * len(m.group(0)), text)
    return found


def _readings(match: re.Match[str], form: str) -> list[frozenset[time]]:
    if form == "noon":
        return [frozenset({time(12, 0)})]
    if form == "midnight":
        return [frozenset({time(0, 0)})]
    groups = match.groupdict()
    if form == "military":
        hour, minute = groups.get("h") or groups["h2"], groups.get("m") or groups.get("m2") or "0"
        return [frozenset({time(int(hour), int(minute))})] if int(hour) < 24 else []
    if form == "half":
        return [_reading(groups["h"], 30, "")]
    if form == "part":
        part = (groups.get("part") or ("evening" if groups.get("night") else "")).lower()
        return [_reading(groups["h"], 0, "" if not part else ("a" if part == "morning" else "p"))]
    if form == "range":
        # An end without its own am/pm takes the other end's ("2-4 pm"), else both readings.
        shared = groups.get("kap") or groups.get("hap") or ""
        return [
            _reading(groups["h"], int(groups.get("hm") or 0), groups.get("hap") or shared),
            _reading(groups["k"], int(groups.get("km") or 0), groups.get("kap") or shared),
        ]
    hour_text, meridiem = groups["h"], groups.get("ap") or ""
    if form == "digits":
        return [_reading(hour_text, int(groups["m"]), meridiem, twenty_four=True)]
    if form == "words" and groups.get("mw"):
        return [_reading(hour_text, _minute_value(groups["mw"]), meridiem)]
    if form == "relative":
        lead = re.sub(r"\s+minutes?$", "", groups["q"].lower()).removeprefix("a ").strip()
        offset = {"half": 30, "quarter": 15}.get(lead)
        if offset is None:
            offset = int(lead) if lead.isdigit() else _minute_value(lead)
        if not 0 < offset < 60:
            return []
        if groups["dir"].lower() in ("to", "till", "before", "of"):
            hour = _hour_value(hour_text)
            return [_reading(str((hour - 1) or 12), 60 - offset, meridiem)]
        return [_reading(hour_text, offset, meridiem)]
    return [_reading(hour_text, 0, meridiem)]


def _hour_value(text: str) -> int:
    return int(text) if text.isdigit() else _HOUR_WORDS[text.lower()]


def _reading(
    hour_text: str, minute: int, meridiem: str, *, twenty_four: bool = False
) -> frozenset[time]:
    hour, meridiem = _hour_value(hour_text), meridiem.lower()
    if minute > 59:
        return frozenset()
    if twenty_four and not meridiem and (hour == 0 or 12 < hour < 24):
        return frozenset({time(hour, minute)})  # unambiguous 24-hour time
    if hour == 0 or hour > 12:
        return frozenset()  # "13 pm" is not a time
    if meridiem:
        return frozenset({time(hour % 12 + (12 if meridiem == "p" else 0), minute)})
    return frozenset({time(hour % 12, minute), time(hour % 12 + 12, minute)})


def _minute_value(text: str) -> int:
    words = re.split(r"[-\s]+", text.lower())
    if words[0] == "oh":
        return _WORD_VALUES.get(words[-1], 60)
    return _word_value(text)
