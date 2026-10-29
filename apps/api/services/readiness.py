"""The activation checklist: is the receptionist ready to take real calls? (slice 16)

`evaluate(ReadinessFacts) -> Readiness` is PURE: facts in, the seven checklist items out
(table-tested). `ReadinessChecker` gathers the facts (a callable over the database and the
calendar status, wired in `core/readiness.py`) and evaluates them. `AgentActivation` is the
only place `agents.active` changes: `activate` re-checks readiness and flips the flag in the
caller's transaction, `deactivate` always works. Messages are fixed owner-facing strings,
never owner or customer text. No FastAPI or SQLAlchemy imports.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from core.errors import AppError
from core.tenant import Tenant
from services.agent import AgentStore, load_agent, require_business


class AgentNotReadyError(AppError):
    status_code = 409
    code = "agent_not_ready"


@dataclass(frozen=True)
class ReadinessFacts:
    """What the checklist is decided from. Gathered, never computed here."""

    agent_name: str
    agent_greeting: str
    active_services: int
    # Any `business_hours` rows. No rows means "not set", never "closed".
    hours_set: bool
    # Google OAuth is configured on this server.
    calendar_configured: bool
    # not_connected | needs_selection | connected | reconnect_needed
    calendar_status: str
    # `escalation_settings.contact_phone` is set.
    escalation_contact: bool
    # Twilio and the public API origin are configured on this server.
    phone_configured: bool
    # connected (any number) | misconfigured (some row, none connected) | not_connected
    phone_status: str
    # A test chat where the agent answered a customer with a real (non-fallback) reply.
    test_chat_done: bool


@dataclass(frozen=True)
class ReadinessItem:
    key: str
    ok: bool
    required: bool
    message: str
    fix_url: str


@dataclass(frozen=True)
class Readiness:
    items: tuple[ReadinessItem, ...]

    @property
    def ready(self) -> bool:
        return all(item.ok for item in self.items if item.required)

    @property
    def failing(self) -> tuple[ReadinessItem, ...]:
        return tuple(item for item in self.items if item.required and not item.ok)


@dataclass(frozen=True)
class ActivationState:
    active: bool
    readiness: Readiness


# What the owner is told about a failing item, in plain words (for `agent_not_ready`).
_PLAIN = {
    "persona": "a name and greeting",
    "services": "an active service",
    "hours": "opening hours",
    "calendar": "Google Calendar",
    "escalation_contact": "an escalation contact",
    "phone": "a phone number",
    "test_chat": "a test conversation",
}

_CALENDAR_PROBLEMS = {
    "not_connected": "Connect Google Calendar so bookings land on your calendar.",
    "needs_selection": "Choose which calendar to book into.",
    "reconnect_needed": "Reconnect Google Calendar: access was lost.",
}
_PHONE_PROBLEMS = {
    "not_connected": "Connect a phone number.",
    "misconfigured": "Your phone number needs attention: test it on the Phone page.",
}


def _item(key: str, ok: bool, ok_message: str, problem: str, fix_url: str) -> ReadinessItem:
    # Every item is required today; `required` exists so a future one can be advisory.
    return ReadinessItem(
        key=key, ok=ok, required=True, message=ok_message if ok else problem, fix_url=fix_url
    )


def _calendar_problem(facts: ReadinessFacts) -> str:
    if not facts.calendar_configured:
        return "Google Calendar isn't available on this server yet."
    return _CALENDAR_PROBLEMS.get(facts.calendar_status, _CALENDAR_PROBLEMS["not_connected"])


def _phone_problem(facts: ReadinessFacts) -> str:
    if not facts.phone_configured:
        return "Phone calls aren't set up on this server yet."
    return _PHONE_PROBLEMS.get(facts.phone_status, _PHONE_PROBLEMS["not_connected"])


def evaluate(facts: ReadinessFacts) -> Readiness:
    persona_ok = bool(facts.agent_name.strip()) and bool(facts.agent_greeting.strip())
    calendar_ok = facts.calendar_configured and facts.calendar_status == "connected"
    phone_ok = facts.phone_configured and facts.phone_status == "connected"
    plural = "" if facts.active_services == 1 else "s"
    return Readiness(
        (
            _item(
                "persona",
                persona_ok,
                "Name and greeting are set.",
                "Give your receptionist a name and a greeting.",
                "/dashboard/agent#persona",
            ),
            _item(
                "services",
                facts.active_services > 0,
                f"{facts.active_services} active service{plural}.",
                "Add at least one active service.",
                "/dashboard/agent#services",
            ),
            _item(
                "hours",
                facts.hours_set,
                "Opening hours are set.",
                "Set your opening hours.",
                "/dashboard/agent#hours",
            ),
            _item(
                "calendar",
                calendar_ok,
                "Google Calendar is connected.",
                _calendar_problem(facts),
                "/dashboard/settings/calendar",
            ),
            _item(
                "escalation_contact",
                facts.escalation_contact,
                "An escalation contact is set.",
                "Add an escalation contact phone number.",
                "/dashboard/agent#escalation",
            ),
            _item(
                "phone",
                phone_ok,
                "A phone number is connected.",
                _phone_problem(facts),
                "/dashboard/phone",
            ),
            _item(
                "test_chat",
                facts.test_chat_done,
                "You've tested your receptionist.",
                "Have a test conversation in the test chat.",
                "/dashboard/agent#test-chat",
            ),
        )
    )


class ReadinessChecker:
    def __init__(self, facts: Callable[[uuid.UUID], Awaitable[ReadinessFacts]]) -> None:
        self._facts = facts

    async def check(self, tenant: Tenant) -> Readiness:
        return evaluate(await self._facts(require_business(tenant).id))


class AgentActivation:
    """Reads and changes whether the agent answers real calls. The business always comes
    from the tenant. Writes happen in the caller's session; the router commits."""

    def __init__(self, store: AgentStore, checker: ReadinessChecker) -> None:
        self._store = store
        self._checker = checker

    async def state(self, tenant: Tenant) -> ActivationState:
        agent = await load_agent(self._store, require_business(tenant))
        active = agent.active
        return ActivationState(active, await self._checker.check(tenant))

    async def activate(self, tenant: Tenant) -> ActivationState:
        agent = await load_agent(self._store, require_business(tenant))
        readiness = await self._checker.check(tenant)
        if not readiness.ready:
            raise AgentNotReadyError(_not_ready_message(readiness))
        if not agent.active:
            agent.active = True
            await self._store.save(agent)
        return ActivationState(True, readiness)

    async def deactivate(self, tenant: Tenant) -> ActivationState:
        agent = await load_agent(self._store, require_business(tenant))
        if agent.active:
            agent.active = False
            await self._store.save(agent)
        return ActivationState(False, await self._checker.check(tenant))


def _not_ready_message(readiness: Readiness) -> str:
    names = [_PLAIN.get(item.key, item.key) for item in readiness.failing]
    if len(names) > 1:
        listed = ", ".join(names[:-1]) + " and " + names[-1]
    else:
        listed = names[0]
    return f"Your receptionist isn't ready to take calls yet. Still needed: {listed}."
