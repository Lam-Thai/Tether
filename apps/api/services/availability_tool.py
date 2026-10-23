"""`check_availability`: the tool that brings real, free start times to the model.

The model sends the service the customer named, a local date it resolved from the
prompt's "Today" (BUSINESS HOURS) as YYYY-MM-DD ("today" and "tomorrow" are resolved
here; anything else is rejected with the expected format and today's date), and
optionally a part of the day or a wanted time. The backend does the rest:

- The service is resolved through `BusinessDirectory.find_service` (`ambiguous` and
  `not_found` come back as results, so the model asks instead of guessing).
- `AvailabilityService.find_slots` validates the date (past, beyond the horizon,
  closed, hours not listed), reads the calendar and returns explicit results.
- `available`: `offered_times` are local "HH:MM" start times, at most three. They
  are the only times of day the reply may state this turn (`Evidence.slot_times`,
  checked by `services/grounding.py`), and they are saved as `offered_slots` in
  `interactions.state` when the reply is shown (slice 11 books from them, after
  re-checking). `no_availability` / `calendar_unavailable` clear any earlier offer.
- `calendar_unavailable` (not connected, needs a reconnect, or down) offers no
  times and flags the conversation for a human.

The operation log gets ids, dates, counts and reason codes only. Runs outside the
store's savepoint (`isolated=False`) because the calendar lookup commits before it
calls Google; its own DB reads run inside `savepoint()`.
"""

import datetime as dt
import re
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager, nullcontext
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

from services.availability import (
    AvailabilityResult,
    AvailabilityService,
    CalendarUnavailable,
    NoAvailability,
    Slot,
    SlotPreference,
    TimeOfDay,
)
from services.business_tools import WEEKDAYS, blank_is_none, iso_date, wall_time
from services.directory import MAX_QUERY_CHARS, Ambiguous, BusinessDirectory, Found, ServiceFact
from services.grounding import Evidence, SourceKind
from services.tools import Tool, ToolContext, ToolRejection, ToolResult

CHECK_AVAILABILITY = "check_availability"
# `interactions.state` key holding the slots offered in the last shown reply.
OFFERED_SLOTS = "offered_slots"

_WALL_TIME = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")
MAX_DATE_CHARS = 40
# The only words resolved by the backend itself; any other date must be YYYY-MM-DD.
_RELATIVE_DAYS = {"today": 0, "tomorrow": 1}


def _wall_time(value: Any) -> Any:
    """Exactly HH:MM (24-hour), the form the tool results use."""
    value = blank_is_none(value)
    if value is None or isinstance(value, dt.time):
        return value
    if isinstance(value, str) and (match := _WALL_TIME.fullmatch(value.strip())):
        return dt.time(int(match.group(1)), int(match.group(2)))
    raise ValueError("Expected a time as HH:MM (24-hour).")


class CheckAvailabilityArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUERY_CHARS),
        Field(description="The service the customer wants, as they named it, or its id."),
    ]
    # Its own name (not `get_business_info`'s `date`), so its description reaches
    # Gemini's response schema, which drops descriptions two tools disagree on.
    appointment_date: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_DATE_CHARS),
        Field(
            description=(
                "The local date to check as YYYY-MM-DD, e.g. 2026-06-04. Work it out from "
                "Today in BUSINESS HOURS."
            )
        ),
    ]
    time_of_day: Annotated[
        Literal["morning", "afternoon", "evening"] | None,
        BeforeValidator(blank_is_none),
        Field(description="The part of the day the customer prefers, if they said one."),
    ] = None
    around: Annotated[
        dt.time | None,
        BeforeValidator(_wall_time),
        Field(description='A specific time the customer asked for, HH:MM 24-hour ("15:00").'),
    ] = None


def resolve_date(text: str, today: dt.date) -> dt.date:
    """YYYY-MM-DD, or "today" / "tomorrow" resolved in the business timezone. Anything
    else is rejected with the expected format and today's date, so the model can
    correct itself on the next round instead of guessing again."""
    value = text.strip().lower()
    if value in _RELATIVE_DAYS:
        return today + dt.timedelta(days=_RELATIVE_DAYS[value])
    try:
        parsed = iso_date(value)
    except ValueError:
        parsed = None
    if isinstance(parsed, dt.date):
        return parsed
    raise ToolRejection(
        "invalid_date",
        fields=["appointment_date"],
        details={"expected": "YYYY-MM-DD", "today": today.isoformat()},
    )


def _service_view(service: ServiceFact) -> dict[str, Any]:
    return {
        "id": str(service.id),
        "name": service.name,
        "duration_minutes": service.duration_minutes,
    }


def _service_evidence(service: ServiceFact, slots: tuple[Slot, ...] = ()) -> Evidence:
    """With no slots, this check found nothing to offer: last turn's offer is stale."""
    return Evidence(
        sources={str(service.id): "service"},
        durations=(service.duration_minutes,),
        slot_times=frozenset(slot.start.time().replace(tzinfo=None) for slot in slots),
        retires_earlier_offer=not slots,
    )


def earlier_offer(state: Mapping[str, Any]) -> tuple[frozenset[dt.time], str | None]:
    """The offer the customer heard last (`interactions.state.offered_slots`): its local
    start times, which a reply may repeat back, and a one-line summary for the prompt.
    Backend-written values only; anything malformed counts as no offer."""
    offer = state.get(OFFERED_SLOTS)
    if not isinstance(offer, Mapping):
        return frozenset(), None
    try:
        starts = [dt.datetime.fromisoformat(slot["start"]) for slot in offer["slots"]]
        day = dt.date.fromisoformat(offer["date"])
    except (KeyError, TypeError, ValueError):
        return frozenset(), None
    if not starts:
        return frozenset(), None
    times = ", ".join(wall_time(start) for start in starts)
    summary = f"{WEEKDAYS[day.weekday()]} {day.isoformat()}: {times}"
    return frozenset(start.time().replace(tzinfo=None) for start in starts), summary


def _slot_state(slot: Slot) -> dict[str, str]:
    return {"start": slot.start.isoformat(), "end": slot.end.isoformat()}


def _result(found: AvailabilityResult, now: dt.datetime) -> ToolResult:
    day = {"date": found.date.isoformat(), "weekday": WEEKDAYS[found.date.weekday()]}
    service = _service_view(found.service)
    log: dict[str, Any] = {"service_ids": [service["id"]], "date": found.date.isoformat()}

    if isinstance(found, CalendarUnavailable):
        return ToolResult(
            for_model={
                "result": "calendar_unavailable",
                "reason": found.reason,
                "service": service,
                **day,
            },
            for_log={**log, "result": "calendar_unavailable", "reason": found.reason},
            evidence=_service_evidence(found.service),
            state={OFFERED_SLOTS: None},
            requires_human=True,
        )
    if isinstance(found, NoAvailability):
        for_model: dict[str, Any] = {
            "result": "no_availability",
            "reason": found.reason,
            "service": service,
            **day,
        }
        if found.earliest is not None and found.latest is not None:
            for_model["bookable_from"] = found.earliest.isoformat()
            for_model["bookable_until"] = found.latest.isoformat()
        return ToolResult(
            for_model=for_model,
            for_log={**log, "result": "no_availability", "reason": found.reason},
            evidence=_service_evidence(found.service),
            state={OFFERED_SLOTS: None},
        )
    return ToolResult(
        for_model={
            "result": "available",
            "service": service,
            **day,
            "timezone": found.timezone,
            "offered_times": [wall_time(slot.start) for slot in found.offered],
            "matched_preference": found.matched_preference,
            "free_starts_that_day": len(found.slots),
        },
        for_log={
            **log,
            "result": "available",
            "offered": len(found.offered),
            "free": len(found.slots),
        },
        evidence=_service_evidence(found.service, found.offered),
        state={
            OFFERED_SLOTS: {
                "service_id": service["id"],
                "date": found.date.isoformat(),
                "timezone": found.timezone,
                "slots": [_slot_state(slot) for slot in found.offered],
                "offered_at": now.isoformat(),
            }
        },
    )


def check_availability_tool(
    directory: BusinessDirectory,
    availability: AvailabilityService,
    *,
    savepoint: Callable[[], AbstractAsyncContextManager[Any]] = nullcontext,
) -> Tool[CheckAvailabilityArgs]:
    async def run(ctx: ToolContext, args: CheckAvailabilityArgs) -> ToolResult:
        day = resolve_date(args.appointment_date, directory.today(ctx.tenant))
        async with savepoint():
            match = await directory.find_service(ctx.tenant, args.service)
        if isinstance(match, Ambiguous):
            ids = [str(service.id) for service in match.candidates]
            sources: dict[str, SourceKind] = {service_id: "service" for service_id in ids}
            return ToolResult(
                for_model={
                    "result": "ambiguous_service",
                    "candidates": [{"id": str(s.id), "name": s.name} for s in match.candidates],
                },
                for_log={"result": "ambiguous_service", "service_ids": ids},
                evidence=Evidence(sources=sources),
            )
        if not isinstance(match, Found):
            return ToolResult(
                for_model={"result": "service_not_found"},
                for_log={"result": "service_not_found", "service_ids": []},
            )
        time_of_day: TimeOfDay | None = args.time_of_day
        preference = SlotPreference(day, time_of_day=time_of_day, around=args.around)
        found = await availability.find_slots(ctx.tenant, match.service.id, preference)
        return _result(found, directory.now(ctx.tenant))

    return Tool(
        name=CHECK_AVAILABILITY,
        description=(
            "Check which start times are really free for a service on one local date "
            "(the business calendar, opening hours and booking rules). Call it before "
            "naming any appointment time, every turn."
        ),
        args=CheckAvailabilityArgs,
        run=run,
        isolated=False,
    )
