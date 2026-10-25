"""A `StoredInteraction` (a `summary` suite case) as the facts the `Summarizer` reads.

Shared by the scorers (which recompute the verified outcome from the seed, never from
the model's text) and the in-memory sandbox. The Postgres sandbox writes real rows.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from evals.cases import StoredBooking, StoredInteraction
from services.summarizer import (
    BookingFact,
    InteractionFacts,
    OperationFact,
    TranscriptLine,
)

SALON_TIMEZONE = "America/New_York"
BOOKING_MINUTES = 45


def booking_start(booking: StoredBooking, timezone: str = SALON_TIMEZONE) -> datetime:
    """The booking's local start as an aware datetime in the business's zone."""
    return datetime.strptime(booking.start, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo(timezone))


def facts_of(stored: StoredInteraction, timezone: str = SALON_TIMEZONE) -> InteractionFacts:
    return InteractionFacts(
        status=stored.status,
        outcome=stored.outcome,
        intent=stored.intent,
        requires_human=stored.requires_human,
        summary=None,
        summary_source=None,
        messages=tuple(TranscriptLine(m.speaker, m.text) for m in stored.messages),
        operations=tuple(OperationFact(o.tool, o.status, o.result) for o in stored.operations),
        bookings=tuple(
            BookingFact(b.service, booking_start(b, timezone), timezone, b.status)
            for b in stored.bookings
        ),
    )
