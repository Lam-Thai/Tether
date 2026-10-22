"""`get_services` and `get_business_info`: the tools that bring business facts to the model.

Both read `BusinessDirectory` (the one source of business facts), scoped by
`ToolContext.tenant`, and hand back only what it verified:

- `get_services(query)`: without a query, the active catalog (at most
  `MAX_LISTED_SERVICES`); with one, `find_service` decides. `found` is that
  service; `ambiguous` lists the candidates' ids and names but never their
  prices, so the model has to ask which one; `not_found` is said out loud, so
  the model has nothing to fill in. Inactive services never appear. Prices
  are decimal strings in the business currency, and null means no listed price.
- `get_business_info(date, days)`: the profile, plus opening windows for up to
  `MAX_INFO_DAYS` local dates from `date` (default today), as local "HH:MM" in
  the business's timezone, each `open`, `closed` or `not_set` (the owner never
  listed hours, which is not "closed"). A start date outside today ..
  today + `MAX_DAYS_AHEAD` is rejected. The prompt already carries the next 7
  days, so this is for dates further out.

Each result's `evidence` is what the grounding guard accepts this turn: the
service ids, prices and durations returned, and `business_hours` only when
hours are listed. A lookup that found nothing, or several candidates, marks
`PRICE_INQUIRY` unresolved, so the honest "we don't list that" / "which one?"
reply needs no source. The operation log gets ids, counts and match kinds,
never names or descriptions (owner text).
"""

import datetime as dt
import re
from collections.abc import Sequence
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

from services.directory import (
    MAX_QUERY_CHARS,
    Ambiguous,
    BusinessDirectory,
    BusinessProfile,
    Closed,
    Found,
    HoursNotSet,
    Open,
    OpeningWindow,
    ServiceFact,
)
from services.grounding import BUSINESS_HOURS, BUSINESS_PROFILE, Evidence, SourceKind
from services.tools import Tool, ToolContext, ToolRejection, ToolResult

GET_SERVICES = "get_services"
GET_BUSINESS_INFO = "get_business_info"

MAX_LISTED_SERVICES = 20
MAX_DESCRIPTION_CHARS = 160
MAX_INFO_DAYS = 14
MAX_DAYS_AHEAD = 365
# Days reported when the model gives no `days`: one for a given date, a week from today.
DEFAULT_DAYS_FOR_DATE = 1
DEFAULT_DAYS_FROM_TODAY = 7

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _blank_is_none(value: Any) -> Any:
    # Gemini sometimes sends "" for "not given"; the executor already drops nulls.
    return None if isinstance(value, str) and not value.strip() else value


def _iso_date(value: Any) -> Any:
    """Exactly YYYY-MM-DD (a real date), never a timestamp or a looser format."""
    value = _blank_is_none(value)
    if value is None or isinstance(value, dt.date):
        return value
    if isinstance(value, str) and _ISO_DATE.fullmatch(value.strip()):
        return dt.date.fromisoformat(value.strip())
    raise ValueError("Expected a date as YYYY-MM-DD.")


class GetServicesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: Annotated[
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUERY_CHARS),
        ]
        | None,
        BeforeValidator(_blank_is_none),
        Field(
            description=(
                "The service the customer asked about, in a few words, or its id. "
                "Leave it out to list every service."
            )
        ),
    ] = None


class GetBusinessInfoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: Annotated[
        dt.date | None,
        BeforeValidator(_iso_date),
        Field(
            description=(
                "First local date to report, YYYY-MM-DD, from today up to a year ahead. "
                "Leave it out to start today."
            )
        ),
    ] = None
    days: Annotated[
        int | None,
        Field(
            strict=True,
            ge=1,
            le=MAX_INFO_DAYS,
            description=(
                f"How many consecutive days to report, 1 to {MAX_INFO_DAYS}. "
                "Leave it out for just that date (or the next 7 days without a date)."
            ),
        ),
    ] = None


def wall_time(moment: dt.datetime | dt.time) -> str:
    """Local "HH:MM" (an `Open` window's datetimes are already in the business's zone)."""
    return moment.strftime("%H:%M")


def window_view(window: OpeningWindow) -> dict[str, str]:
    """One local date's opening window, as the model sees it in results."""
    view = {"date": window.local_date.isoformat(), "weekday": WEEKDAYS[window.local_date.weekday()]}
    if isinstance(window, Open):
        return {
            **view,
            "status": "open",
            "opens": wall_time(window.start),
            "closes": wall_time(window.end),
        }
    if isinstance(window, Closed):
        return {**view, "status": "closed"}
    return {**view, "status": "not_set"}


def _service_view(service: ServiceFact) -> dict[str, Any]:
    description = (service.description or "").strip()
    return {
        "id": str(service.id),
        "name": service.name,
        "duration_minutes": service.duration_minutes,
        "price": str(service.price) if service.price is not None else None,
        "description": description[:MAX_DESCRIPTION_CHARS] or None,
    }


def _service_evidence(services: Sequence[ServiceFact]) -> Evidence:
    return Evidence(
        sources={str(service.id): "service" for service in services},
        prices=tuple(service.price for service in services if service.price is not None),
        durations=tuple(service.duration_minutes for service in services),
    )


def _profile_view(profile: BusinessProfile) -> dict[str, Any]:
    return {
        "name": profile.name,
        "address": profile.address,
        "phone": profile.phone,
        "email": profile.contact_email,
        "timezone": profile.timezone,
        "currency": profile.currency,
    }


def get_services_tool(directory: BusinessDirectory) -> Tool[GetServicesArgs]:
    async def run(ctx: ToolContext, args: GetServicesArgs) -> ToolResult:
        currency = directory.profile(ctx.tenant).currency
        if args.query is None:
            catalog = await directory.services(ctx.tenant)
            shown = catalog[:MAX_LISTED_SERVICES]
            ids = [str(service.id) for service in shown]
            for_model: dict[str, Any] = {"match": "list", "count": len(shown)}
            if shown:
                for_model |= {"currency": currency, "services": [_service_view(s) for s in shown]}
            else:
                for_model["result"] = "the business lists no services"
            if len(catalog) > len(shown):
                for_model["not_shown"] = len(catalog) - len(shown)
            return ToolResult(
                for_model=for_model,
                for_log={"match": "list", "service_ids": ids, "count": len(catalog)},
                evidence=_service_evidence(shown),
            )

        match = await directory.find_service(ctx.tenant, args.query)
        if isinstance(match, Found):
            return ToolResult(
                for_model={
                    "match": "found",
                    "currency": currency,
                    "service": _service_view(match.service),
                },
                for_log={"match": "found", "service_ids": [str(match.service.id)]},
                evidence=_service_evidence([match.service]),
            )
        if isinstance(match, Ambiguous):
            ids = [str(service.id) for service in match.candidates]
            sources: dict[str, SourceKind] = {service_id: "service" for service_id in ids}
            return ToolResult(
                for_model={
                    "match": "ambiguous",
                    "candidates": [{"id": str(s.id), "name": s.name} for s in match.candidates],
                    "result": "several services match: ask which one the customer means",
                },
                for_log={"match": "ambiguous", "service_ids": ids},
                # Candidate ids, but no prices or durations: those come with a found service.
                evidence=Evidence(sources=sources, unresolved=frozenset({"PRICE_INQUIRY"})),
            )
        return ToolResult(
            for_model={"match": "not_found", "result": "the business does not list this service"},
            for_log={"match": "not_found", "service_ids": []},
            evidence=Evidence(unresolved=frozenset({"PRICE_INQUIRY"})),
        )

    return Tool(
        name=GET_SERVICES,
        description=(
            "Look up this business's services with their prices and durations. "
            "Pass the service the customer named as query; leave it out to list them all."
        ),
        args=GetServicesArgs,
        run=run,
    )


def get_business_info_tool(directory: BusinessDirectory) -> Tool[GetBusinessInfoArgs]:
    async def run(ctx: ToolContext, args: GetBusinessInfoArgs) -> ToolResult:
        today = directory.today(ctx.tenant)
        latest = today + dt.timedelta(days=MAX_DAYS_AHEAD)
        start = args.date or today
        if not today <= start <= latest:
            # Checked before any read: the model only learns the range it may ask for.
            raise ToolRejection(
                "date_out_of_range",
                fields=["date"],
                details={"earliest": today.isoformat(), "latest": latest.isoformat()},
            )
        wanted = args.days or (DEFAULT_DAYS_FOR_DATE if args.date else DEFAULT_DAYS_FROM_TODAY)
        windows = await directory.schedule(
            ctx.tenant, start, min(wanted, (latest - start).days + 1)
        )
        profile = directory.profile(ctx.tenant)
        listed = any(not isinstance(window, HoursNotSet) for window in windows)

        for_model: dict[str, Any] = {
            "business": _profile_view(profile),
            "today": today.isoformat(),
            "hours_listed": listed,
            "days": [window_view(window) for window in windows],
        }
        if not listed:
            for_model["result"] = "the business has not listed its opening hours"
        sources: dict[str, SourceKind] = {BUSINESS_PROFILE: "business_profile"}
        if listed:
            sources[BUSINESS_HOURS] = "business_hours"
        counts = {
            status: sum(1 for view in for_model["days"] if view["status"] == status)
            for status in ("open", "closed", "not_set")
        }
        return ToolResult(
            for_model=for_model,
            for_log={"start": start.isoformat(), "days": len(windows), **counts},
            evidence=Evidence(sources=sources),
        )

    return Tool(
        name=GET_BUSINESS_INFO,
        description=(
            "Get this business's contact details and its opening hours on given dates "
            "(local time). Use it for dates beyond the next 7 days in BUSINESS HOURS."
        ),
        args=GetBusinessInfoArgs,
        run=run,
    )
