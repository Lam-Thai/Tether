"""`create_booking`: the one tool that makes an appointment, through `BookingService.book`.

The model sends the service, the local date (YYYY-MM-DD, as for `check_availability`),
the start time (HH:MM) the customer picked, and the customer's name (and phone, if
given; on voice it will come from caller ID). Required details are collected one at a
time by the model; a call missing one is rejected by the args model and never runs.

What the model hears back is only what the backend verified:

- `confirmed`: the booking, as stored (service, date, start/end, name). Its start time
  is the only time the reply may state (`Evidence.slot_times`), and `booking_confirmed`
  is the only thing that lets a reply say "you're booked" (`services/grounding.py`).
- `slot_taken`: the start isn't free (taken on the calendar, by another caller, or off
  the slot grid); `offered_times` are the free alternatives, which become the new offer
  (`offered_slots`), exactly like `check_availability`.
- `invalid`: a fixed reason (`missing_name`, `invalid_phone`, `closed`, `past_date`, ...).
- `ambiguous_service` / `service_not_found`: ask which one, as `check_availability`.
- `booking_failed`: the calendar couldn't be used, or the booking couldn't be saved.
  Nothing was booked; the conversation is flagged for a person.

The operation log gets ids, the date, the status and reason codes only. Runs outside the
store's savepoint (`isolated=False`): the booking commits its reservation before Google
is called and isolates its own DB work.
"""

import datetime as dt
import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, nullcontext
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

from services.availability_tool import (
    OFFERED_SLOTS,
    AppointmentDateArg,
    ServiceArg,
    offer_state,
    resolve_date,
    service_evidence,
    service_view,
)
from services.booking import (
    MAX_NAME_CHARS,
    MAX_PHONE_CHARS,
    BookedAppointment,
    BookingOutcome,
    BookingRequest,
    BookingService,
    CalendarFailed,
    Confirmed,
    Invalid,
)
from services.business_tools import WEEKDAYS, blank_is_none, wall_time
from services.directory import Ambiguous, BusinessDirectory, Found
from services.grounding import Evidence, SourceKind
from services.tools import Tool, ToolContext, ToolResult

CREATE_BOOKING = "create_booking"

_START_TIME = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")


def _start_time(value: Any) -> Any:
    """Exactly H:MM / HH:MM (24-hour), the form `offered_times` use."""
    if isinstance(value, dt.time):
        return value
    if isinstance(value, str) and (match := _START_TIME.fullmatch(value.strip())):
        return dt.time(int(match.group(1)), int(match.group(2)))
    raise ValueError("Expected a start time as HH:MM (24-hour).")


class CreateBookingArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: ServiceArg
    appointment_date: AppointmentDateArg
    start_time: Annotated[
        dt.time,
        BeforeValidator(_start_time),
        Field(
            description=(
                "The start time the customer chose, HH:MM 24-hour, exactly as in offered_times."
            )
        ),
    ]
    customer_name: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_NAME_CHARS),
        Field(description="The name the customer gave for the booking."),
    ]
    customer_phone: Annotated[
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_PHONE_CHARS)] | None,
        BeforeValidator(blank_is_none),
        Field(description="The customer's phone number, only if they gave one."),
    ] = None


def _confirmed(outcome: Confirmed) -> ToolResult:
    booking = outcome.booking
    day = booking.start.date()
    service_id = str(booking.service_id) if booking.service_id else None
    minutes = round((booking.end - booking.start).total_seconds() / 60)
    sources: dict[str, SourceKind] = {service_id: "service"} if service_id else {}
    return ToolResult(
        for_model={
            "result": "confirmed",
            "booking": {
                "service": booking.service_name,
                "date": day.isoformat(),
                "weekday": WEEKDAYS[day.weekday()],
                "start_time": wall_time(booking.start),
                "end_time": wall_time(booking.end),
                "duration_minutes": minutes,
                "customer_name": booking.customer_name,
            },
        },
        for_log={
            "result": "confirmed",
            "booking_id": str(booking.id),
            "service_ids": [service_id] if service_id else [],
            "date": day.isoformat(),
            "replayed": outcome.replayed,
        },
        evidence=Evidence(
            sources=sources,
            durations=(minutes,),
            slot_times=frozenset({booking.start.time().replace(tzinfo=None)}),
            booking_confirmed=True,
        ),
        # The offer has been used: nothing is on offer any more.
        state={OFFERED_SLOTS: None},
        confirmation=confirmation_line(booking),
    )


def confirmation_line(booking: BookedAppointment) -> str:
    """What the customer is told if the model's own reply can't be shown: the booking
    exactly as stored (never a fallback that would deny it)."""
    start = booking.start
    hour = start.hour % 12 or 12
    clock = f"{hour}:{start.minute:02d} {'AM' if start.hour < 12 else 'PM'}"
    day = f"{WEEKDAYS[start.weekday()]}, {start.strftime('%B')} {start.day}"
    return (
        f"You're booked: {booking.service_name} on {day} at {clock}, "
        f"under the name {booking.customer_name}."
    )


def _result(outcome: BookingOutcome, day: dt.date, now: dt.datetime) -> ToolResult:
    log: dict[str, Any] = {"date": day.isoformat()}
    if isinstance(outcome, Confirmed):
        return _confirmed(outcome)
    if isinstance(outcome, Invalid):
        for_model: dict[str, Any] = {"result": "invalid", "reason": outcome.reason}
        if outcome.earliest is not None and outcome.latest is not None:
            for_model["bookable_from"] = outcome.earliest.isoformat()
            for_model["bookable_until"] = outcome.latest.isoformat()
        evidence = service_evidence(outcome.service) if outcome.service else Evidence()
        return ToolResult(
            for_model=for_model,
            for_log={**log, "result": "invalid", "reason": outcome.reason},
            evidence=evidence,
        )
    if isinstance(outcome, CalendarFailed):
        return ToolResult(
            for_model={"result": "booking_failed", "reason": outcome.reason},
            for_log={**log, "result": "booking_failed", "reason": outcome.reason},
            evidence=service_evidence(outcome.service) if outcome.service else Evidence(),
            state={OFFERED_SLOTS: None},
            requires_human=True,
        )
    # Slot taken: the alternatives are a fresh, verified offer.
    service = service_view(outcome.service)
    state = (
        offer_state(outcome.service, outcome.date, outcome.timezone, outcome.alternatives, now)
        if outcome.alternatives
        else None
    )
    return ToolResult(
        for_model={
            "result": "slot_taken",
            "service": service,
            "date": outcome.date.isoformat(),
            "weekday": WEEKDAYS[outcome.date.weekday()],
            "offered_times": [wall_time(slot.start) for slot in outcome.alternatives],
        },
        for_log={
            **log,
            "result": "slot_taken",
            "service_ids": [service["id"]],
            "offered": len(outcome.alternatives),
        },
        evidence=service_evidence(outcome.service, outcome.alternatives),
        state={OFFERED_SLOTS: state},
    )


def create_booking_tool(
    directory: BusinessDirectory,
    bookings: BookingService,
    *,
    savepoint: Callable[[], AbstractAsyncContextManager[Any]] = nullcontext,
) -> Tool[CreateBookingArgs]:
    async def run(ctx: ToolContext, args: CreateBookingArgs) -> ToolResult:
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
        outcome = await bookings.book(
            ctx.tenant,
            BookingRequest(
                interaction_id=ctx.interaction_id,
                service_id=match.service.id,
                date=day,
                start_time=args.start_time,
                customer_name=args.customer_name,
                customer_phone=args.customer_phone or ctx.caller_phone,
            ),
        )
        return _result(outcome, day, directory.now(ctx.tenant))

    return Tool(
        name=CREATE_BOOKING,
        description=(
            "Book an appointment once the customer has chosen a service, a date and one of "
            "the offered start times, and given their name. Only a confirmed result means "
            "the appointment is booked."
        ),
        args=CreateBookingArgs,
        run=run,
        isolated=False,
    )
