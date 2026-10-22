"""`BusinessDirectory`: the one source of business facts, for the AI and for booking.

Small interface over everything that turns stored rows into facts a
customer may be told:

- `profile(tenant)`: name, address, contact details, timezone, currency.
- `services(tenant, active_only=True)`: the catalog. Inactive services are
  never offered, priced or booked; `active_only=False` exists only for the
  owner's own management screens.
- `find_service(tenant, name_or_id)`: `Found | Ambiguous | NotFound`. Hides
  the matching: id, then case-insensitive exact name, then word/substring
  matches, then a small built-in synonym map and typo-tolerant fuzzy
  matching. Two or more equally good candidates are `Ambiguous` ("cut" when
  both "Haircut" and "Kids cut" exist), never a silent pick. `Found` needs
  every word of the query to be a whole word of the name (or a close typo of
  one): a match on part of a word ("men" in "Women's cut") or one that leaves
  a query word unexplained ("gel manicure" vs "Manicure") is `Ambiguous`, so
  the agent asks instead of pricing the wrong service. Active only.
- `opening_window(tenant, local_date)`: `Open(start, end) | Closed |
  HoursNotSet` in the business's timezone. `start`/`end` are aware datetimes,
  DST-safe (a window across a transition has its real length). `HoursNotSet`
  means the owner never saved hours, which is not "closed".
- `schedule(tenant, start, days)`: `opening_window` for consecutive local
  dates (the prompt's 7-day block and `get_business_info`).
- `today(tenant)`: the current local date in the business's timezone.

Booking (Slices 10-11) depends on this module and never reads the tables
directly. Every read is scoped by `tenant`, never by an id from the model or
the client. Reads the store on every call: an edit applies to the next turn.
No FastAPI or SQLAlchemy imports; the store is bound to a session upstream.
"""

import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Protocol
from zoneinfo import ZoneInfo

from core.tenant import Tenant
from models.hours import BusinessHours
from models.service import Service
from services.agent import require_business

MAX_SCHEDULE_DAYS = 31
MAX_QUERY_CHARS = 100
MAX_CANDIDATES = 5
# Fuzzy matching: a candidate needs this score (0..1), and anything within
# `_FUZZY_TIE` of the best one is just as plausible, so the result is Ambiguous.
_FUZZY_MIN = 0.8
_FUZZY_TIE = 0.05

# Words customers wrap around a service name ("how much is a beard trim?").
_STOPWORDS = frozenset(
    "a an and the for of with my your our please how much is are does do what whats "
    "price prices cost costs get one some any service services i want to book".split()
)
# Spelling variants and short forms, to one canonical (singular) token. Kept
# small and unsurprising: a wrong synonym would make a lookup confidently wrong.
_SYNONYMS = {
    "colour": "color",
    "kids": "kid",
    "child": "kid",
    "children": "kid",
    "childrens": "kid",
    "mani": "manicure",
    "pedi": "pedicure",
    "blowdry": "blow dry",
    "blowout": "blow dry",
}
_NON_WORD = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class BusinessProfile:
    name: str
    industry: str
    address: str
    timezone: str
    currency: str
    phone: str | None
    contact_email: str | None


@dataclass(frozen=True)
class ServiceFact:
    id: uuid.UUID
    name: str
    description: str | None
    duration_minutes: int
    # In `BusinessProfile.currency`; None = no listed price.
    price: Decimal | None
    active: bool


@dataclass(frozen=True)
class Found:
    service: ServiceFact


@dataclass(frozen=True)
class Ambiguous:
    # Best first; at most `MAX_CANDIDATES`, all active.
    candidates: tuple[ServiceFact, ...]


@dataclass(frozen=True)
class NotFound:
    pass


ServiceMatch = Found | Ambiguous | NotFound


@dataclass(frozen=True)
class Open:
    local_date: date
    # Aware datetimes in the business's timezone. Subtract them via UTC
    # (`minutes`): same-tzinfo datetime arithmetic ignores DST offsets.
    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end.astimezone(UTC) - self.start.astimezone(UTC)).total_seconds() // 60)


@dataclass(frozen=True)
class Closed:
    local_date: date


@dataclass(frozen=True)
class HoursNotSet:
    local_date: date


OpeningWindow = Open | Closed | HoursNotSet


@dataclass(frozen=True)
class WeeklyHours:
    """One weekday's stored hours (0 = Monday), for prompts and the owner UI."""

    day_of_week: int
    is_closed: bool
    opens_at: time | None
    closes_at: time | None


class DirectoryStore(Protocol):
    async def list_services(
        self, business_id: uuid.UUID, *, active_only: bool
    ) -> Sequence[Service]:
        """Ordered by name, case-insensitively."""
        ...

    async def list_hours(self, business_id: uuid.UUID) -> Sequence[BusinessHours]:
        """All stored weekday rows (0 or 7), Monday first."""
        ...


class BusinessDirectory:
    def __init__(self, store: DirectoryStore, clock: Callable[[], datetime]) -> None:
        """`clock` returns an aware "now" (UTC); injected so tests can pin dates."""
        self._store = store
        self._clock = clock

    def profile(self, tenant: Tenant) -> BusinessProfile:
        business = require_business(tenant)
        return BusinessProfile(
            name=business.name,
            industry=business.industry,
            address=business.address,
            timezone=business.timezone,
            # NOT NULL with a server default; only an unflushed test object lacks it.
            currency=business.currency or "USD",
            phone=business.phone,
            contact_email=business.contact_email,
        )

    async def services(self, tenant: Tenant, active_only: bool = True) -> list[ServiceFact]:
        business = require_business(tenant)
        rows = await self._store.list_services(business.id, active_only=active_only)
        return [_fact(row) for row in rows]

    async def find_service(self, tenant: Tenant, name_or_id: str) -> ServiceMatch:
        query = name_or_id.strip()[:MAX_QUERY_CHARS]
        catalog = await self.services(tenant)
        try:
            wanted = uuid.UUID(query)
        except ValueError:
            return _match_by_name(query, catalog)
        found = [service for service in catalog if service.id == wanted]
        return Found(found[0]) if found else NotFound()

    async def weekly_hours(self, tenant: Tenant) -> list[WeeklyHours]:
        business = require_business(tenant)
        rows = await self._store.list_hours(business.id)
        return [
            WeeklyHours(row.day_of_week, row.is_closed, row.opens_at, row.closes_at)
            for row in sorted(rows, key=lambda row: row.day_of_week)
        ]

    async def opening_window(self, tenant: Tenant, local_date: date) -> OpeningWindow:
        return (await self.schedule(tenant, local_date, 1))[0]

    async def schedule(self, tenant: Tenant, start: date, days: int) -> list[OpeningWindow]:
        """One store read for the whole range; `days` is clamped to 1..MAX_SCHEDULE_DAYS."""
        business = require_business(tenant)
        week = {day.day_of_week: day for day in await self.weekly_hours(tenant)}
        zone = ZoneInfo(business.timezone)
        count = max(1, min(days, MAX_SCHEDULE_DAYS))
        return [_window(week, zone, start + timedelta(days=n)) for n in range(count)]

    def today(self, tenant: Tenant) -> date:
        business = require_business(tenant)
        return self._clock().astimezone(ZoneInfo(business.timezone)).date()


def _fact(row: Service) -> ServiceFact:
    return ServiceFact(
        id=row.id,
        name=row.name,
        description=row.description,
        duration_minutes=row.duration_minutes,
        price=row.price,
        active=row.active,
    )


def _window(week: dict[int, WeeklyHours], zone: ZoneInfo, local_date: date) -> OpeningWindow:
    day = week.get(local_date.weekday())
    if day is None:
        return HoursNotSet(local_date)
    if day.is_closed or day.opens_at is None or day.closes_at is None:
        return Closed(local_date)
    start = _instant(local_date, day.opens_at, zone)
    return Open(local_date, start, _instant(local_date, day.closes_at, zone))


def _instant(local_date: date, wall: time, zone: ZoneInfo) -> datetime:
    """The real instant for a local wall time. A time inside the spring-forward gap
    (02:30 when clocks jump 02:00 -> 03:00) doesn't exist: the round trip through
    UTC moves it to the wall time that does (03:30). A repeated fall-back time
    takes its first occurrence."""
    return datetime.combine(local_date, wall, tzinfo=zone).astimezone(UTC).astimezone(zone)


def _tokens(text: str) -> list[str]:
    """Lowercase words with punctuation, filler words and plurals normalized away,
    and spelling variants mapped through `_SYNONYMS`."""
    words = _NON_WORD.sub(" ", text.lower().replace("'", "").replace("&", " and ")).split()
    out: list[str] = []
    for word in words:
        word = _SYNONYMS.get(word, word)
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = _SYNONYMS.get(word[:-1], word[:-1])
        out.extend(part for part in word.split() if part not in _STOPWORDS)
    return out


def _match_by_name(query: str, catalog: Sequence[ServiceFact]) -> ServiceMatch:
    """Exact (ignoring case, spacing and punctuation), then every query word in
    the name, then fuzzy. The first tier that finds anything decides. Only a
    single hit that accounts for every query word with a whole (or near-identical)
    name word is Found; anything weaker is Ambiguous. Never falls through to a guess."""
    wanted = _tokens(query)
    if not wanted or not catalog:
        return NotFound()
    joined = "".join(wanted)
    names = [(service, _tokens(service.name)) for service in catalog]

    exact = [service for service, tokens in names if "".join(tokens) == joined]
    if exact:
        return _decide(exact)

    def contains(tokens: list[str], word: str) -> bool:
        return word in tokens or (len(word) >= 3 and any(word in token for token in tokens))

    partial = [(s, tokens) for s, tokens in names if all(contains(tokens, w) for w in wanted)]
    if partial:
        sure = len(partial) == 1 and all(word in partial[0][1] for word in wanted)
        return _decide([service for service, _ in partial], sure=sure)

    scored = [(_fuzzy(wanted, tokens), service, tokens) for service, tokens in names if tokens]
    best = max((score for score, _, _ in scored), default=0.0)
    if best < _FUZZY_MIN:
        return NotFound()
    close = sorted(
        ((score, s, tokens) for score, s, tokens in scored if score >= best - _FUZZY_TIE),
        key=lambda item: -item[0],
    )
    sure = len(close) == 1 and all(
        max(SequenceMatcher(None, word, token).ratio() for token in close[0][2]) >= _FUZZY_MIN
        for word in wanted
    )
    return _decide([service for _, service, _ in close], sure=sure)


def _fuzzy(wanted: list[str], tokens: list[str]) -> float:
    whole = SequenceMatcher(None, "".join(wanted), "".join(tokens)).ratio()
    per_word = sum(
        max(SequenceMatcher(None, word, token).ratio() for token in tokens) for word in wanted
    ) / len(wanted)
    return max(whole, per_word)


def _decide(candidates: Sequence[ServiceFact], *, sure: bool = True) -> ServiceMatch:
    if len(candidates) == 1 and sure:
        return Found(candidates[0])
    return Ambiguous(tuple(candidates[:MAX_CANDIDATES]))
