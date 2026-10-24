"""The scripted Google Calendar of an eval case (`calendar:` in the case YAML).

Every case gets its own `FakeGoogleCalendarApi` that knows the sandbox tenant's one
refresh token, so cases running at once never see each other's busy time. Everything is
synthetic and relative to `EVAL_NOW` (Wednesday 2026-06-03 14:00 America/New_York) and
the demo salon's hours (Tue/Wed/Fri 09-18, Thu 09-20, Sat 10-16, closed Sun and Mon).

- `open`: nothing busy.
- `busy`: a partly booked week (below), so each asked-for day has a few real gaps.
- `fully_booked`: every minute of the next 14 days is taken.
- `down`: free/busy fails with a retryable server error every time.
- `booking_fails`: the `busy` week, but creating an event fails with a retryable server
  error every time (free/busy still works, so the slot looks free and the write fails).
- `disconnected`: no connection at all (the sandbox runs the case with Google Calendar
  unconfigured, so the lookup answers `not_connected`).

The `busy` week, as free gaps for a 45-minute Haircut (15-minute grid anchored at
opening, no buffer; the cases' `expected_times` are these starts):

- Thu 06-04: free 11:00-12:00 and 17:30-18:30 -> 11:00, 11:15, 17:30, 17:45
- Fri 06-05: free 14:00-15:30 -> 14:00, 14:15, 14:30, 14:45 (15:00 does not fit)
- Sat 06-06: free 13:00-14:00 -> 13:00, 13:15
- Tue 06-09: free 09:00-10:30 -> 09:00, 09:15, 09:30, 09:45
- every other day: busy only 12:00-13:00 (lunch), a wide-open day
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from integrations.google_calendar.fake import FakeGoogleCalendarApi, fake_calendar
from integrations.google_calendar.port import GoogleApiError

ZONE = ZoneInfo("America/New_York")
CALENDAR_ID = "eval-salon@example.test"
CALENDAR_NAME = "Eval salon (synthetic)"
# The one refresh token the sandbox tenant's connection row holds (sealed in the vault).
REFRESH_TOKEN = "eval-refresh-token"
FULL_BOOKING_DAYS = 14
# Enough failures for every lookup a case can make (3 rounds x 3 calls x 2 attempts).
DOWN_FAILURES = 40

# Local busy blocks (HH:MM-HH:MM) of the `busy` week, by date.
_BUSY_WEEK: dict[date, tuple[tuple[str, str], ...]] = {
    date(2026, 6, 4): (("09:00", "11:00"), ("12:00", "17:30"), ("18:30", "20:00")),
    date(2026, 6, 5): (("09:00", "14:00"), ("15:30", "18:00")),
    date(2026, 6, 6): (("10:00", "13:00"), ("14:00", "16:00")),
    date(2026, 6, 9): (("10:30", "18:00"),),
}
_LUNCH = (("12:00", "13:00"),)


def _local(day: date, hhmm: str) -> datetime:
    hour, minute = map(int, hhmm.split(":"))
    return datetime.combine(day, time(hour, minute), tzinfo=ZONE).astimezone(UTC)


async def scripted_calendar(mode: str, now: datetime) -> FakeGoogleCalendarApi:
    """A fake Google account that holds `REFRESH_TOKEN` and one owned calendar, scripted
    for `mode` (the OAuth exchange the owner would have done is replayed on the fake)."""
    api = FakeGoogleCalendarApi()
    api.issue_grant(
        refresh_token=REFRESH_TOKEN,
        calendars=[fake_calendar(CALENDAR_ID, CALENDAR_NAME, primary=True)],
    )
    await api.exchange_code(
        code="eval", code_verifier="eval", redirect_uri="https://eval.example.test"
    )
    today = now.astimezone(ZONE).date()
    if mode == "down":
        api.fail_next(
            "free_busy", GoogleApiError("server_error", retryable=True), times=DOWN_FAILURES
        )
    if mode == "fully_booked":
        for offset in range(FULL_BOOKING_DAYS + 1):
            day = today + timedelta(days=offset)
            api.add_busy(
                REFRESH_TOKEN,
                CALENDAR_ID,
                _local(day, "00:00"),
                _local(day + timedelta(days=1), "00:00"),
            )
    elif mode in ("busy", "booking_fails"):
        for offset in range(1, FULL_BOOKING_DAYS + 1):
            day = today + timedelta(days=offset)
            for begin, end in _BUSY_WEEK.get(day, _LUNCH):
                api.add_busy(REFRESH_TOKEN, CALENDAR_ID, _local(day, begin), _local(day, end))
    if mode == "booking_fails":
        api.fail_next(
            "insert_event", GoogleApiError("server_error", retryable=True), times=DOWN_FAILURES
        )
    return api
