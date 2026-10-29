"""Best-effort cancellation of Google Calendar events Tether created, shared by "delete test
data" (`services/test_data.py`) and "delete account" (`services/account.py`).

`cancel_events` returns the event ids that are gone from the calendar afterwards (cancelled,
or already gone). It never raises a calendar error: the callers report what could not be
cancelled so the owner can remove those events in Google Calendar. The deadline is checked
BETWEEN calls only, never by interrupting one (that could cancel a call mid-database-work on
the shared request session); each call is already bounded by `GOOGLE_CALENDAR_TIMEOUT_SECONDS`
and `CALENDAR_RETRY`. No FastAPI or SQLAlchemy imports.
"""

import time
import uuid
from collections.abc import Awaitable, Callable

from services.calendar import (
    CalendarAuthRevokedError,
    CalendarError,
    CalendarNotFoundError,
    CalendarNotSelectedError,
)

MAX_EVENT_CANCELS = 50
DEADLINE_SECONDS = 15.0


async def cancel_events(
    cancel_event: Callable[[uuid.UUID, str], Awaitable[None]],
    business_id: uuid.UUID,
    event_ids: list[str],
    *,
    deadline_seconds: float = DEADLINE_SECONDS,
    max_cancels: int = MAX_EVENT_CANCELS,
    monotonic: Callable[[], float] = time.monotonic,
) -> set[str]:
    """Tries each event id in order, at most `max_cancels` of them. A not-found counts as
    cancelled (already gone is what the owner wanted); a revoked or unselected calendar
    stops everything (every further attempt would fail the same way); any other calendar
    error counts that one as not cancelled and carries on."""
    deadline = monotonic() + deadline_seconds
    cancelled: set[str] = set()
    for event_id in event_ids[:max_cancels]:
        if monotonic() >= deadline:
            break  # out of time; what remains is reported as not cancelled
        try:
            await cancel_event(business_id, event_id)
        except CalendarNotFoundError:
            cancelled.add(event_id)  # already gone: what the owner wanted
        except (CalendarNotSelectedError, CalendarAuthRevokedError):
            break  # every further attempt would fail the same way
        except CalendarError:
            continue
        else:
            cancelled.add(event_id)
    return cancelled
