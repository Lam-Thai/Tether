"""The activation checklist: `evaluate` as a table (every item, every failing variant, the
required / ready logic), `ReadinessChecker` over a scripted facts callable, and
`AgentActivation` on an in-memory agent store. No DB, no provider."""

import uuid
from dataclasses import dataclass, replace

import pytest

from core.tenant import Tenant
from models.agent import Agent
from models.business import Business
from models.user import User
from services.business import BusinessNotFoundError
from services.readiness import (
    AgentActivation,
    AgentNotReadyError,
    Readiness,
    ReadinessChecker,
    ReadinessFacts,
    ReadinessItem,
    evaluate,
)

ALL_GOOD = ReadinessFacts(
    agent_name="Riley",
    agent_greeting="Hi, thanks for calling!",
    active_services=2,
    hours_set=True,
    calendar_configured=True,
    calendar_status="connected",
    escalation_contact=True,
    phone_configured=True,
    phone_status="connected",
    test_chat_done=True,
)

KEYS = ["persona", "services", "hours", "calendar", "escalation_contact", "phone", "test_chat"]
FIX_URLS = {
    "persona": "/dashboard/agent#persona",
    "services": "/dashboard/agent#services",
    "hours": "/dashboard/agent#hours",
    "calendar": "/dashboard/settings/calendar",
    "escalation_contact": "/dashboard/agent#escalation",
    "phone": "/dashboard/phone",
    "test_chat": "/dashboard/agent#test-chat",
}


def _item(readiness: Readiness, key: str) -> ReadinessItem:
    return next(item for item in readiness.items if item.key == key)


def test_everything_in_place_is_ready_with_the_fixed_items_in_order():
    readiness = evaluate(ALL_GOOD)

    assert readiness.ready is True
    assert [item.key for item in readiness.items] == KEYS
    assert all(item.ok and item.required for item in readiness.items)
    assert {item.key: item.fix_url for item in readiness.items} == FIX_URLS
    assert {item.key: item.message for item in readiness.items} == {
        "persona": "Name and greeting are set.",
        "services": "2 active services.",
        "hours": "Opening hours are set.",
        "calendar": "Google Calendar is connected.",
        "escalation_contact": "An escalation contact is set.",
        "phone": "A phone number is connected.",
        "test_chat": "You've tested your receptionist.",
    }


def test_one_active_service_is_singular():
    assert _item(evaluate(replace(ALL_GOOD, active_services=1)), "services").message == (
        "1 active service."
    )


# (item, facts that break only that item, the message it then shows)
FAILING = [
    pytest.param(
        "persona",
        dict(agent_name=""),
        "Give your receptionist a name and a greeting.",
        id="persona-name-empty",
    ),
    pytest.param(
        "persona",
        dict(agent_name="   "),
        "Give your receptionist a name and a greeting.",
        id="persona-name-blank",
    ),
    pytest.param(
        "persona",
        dict(agent_greeting=""),
        "Give your receptionist a name and a greeting.",
        id="persona-greeting-empty",
    ),
    pytest.param(
        "persona",
        dict(agent_greeting=" \n"),
        "Give your receptionist a name and a greeting.",
        id="persona-greeting-blank",
    ),
    pytest.param(
        "services",
        dict(active_services=0),
        "Add at least one active service.",
        id="services-none-active",
    ),
    pytest.param("hours", dict(hours_set=False), "Set your opening hours.", id="hours-not-set"),
    pytest.param(
        "calendar",
        dict(calendar_configured=False, calendar_status="not_connected"),
        "Google Calendar isn't available on this server yet.",
        id="calendar-unconfigured",
    ),
    pytest.param(
        "calendar",
        dict(calendar_configured=False, calendar_status="connected"),
        "Google Calendar isn't available on this server yet.",
        id="calendar-unconfigured-even-with-a-stale-connected-row",
    ),
    pytest.param(
        "calendar",
        dict(calendar_status="not_connected"),
        "Connect Google Calendar so bookings land on your calendar.",
        id="calendar-not-connected",
    ),
    pytest.param(
        "calendar",
        dict(calendar_status="needs_selection"),
        "Choose which calendar to book into.",
        id="calendar-needs-selection",
    ),
    pytest.param(
        "calendar",
        dict(calendar_status="reconnect_needed"),
        "Reconnect Google Calendar: access was lost.",
        id="calendar-reconnect-needed",
    ),
    pytest.param(
        "escalation_contact",
        dict(escalation_contact=False),
        "Add an escalation contact phone number.",
        id="escalation-contact-missing",
    ),
    pytest.param(
        "phone",
        dict(phone_configured=False, phone_status="not_connected"),
        "Phone calls aren't set up on this server yet.",
        id="phone-unconfigured",
    ),
    pytest.param(
        "phone",
        dict(phone_configured=False, phone_status="connected"),
        "Phone calls aren't set up on this server yet.",
        id="phone-unconfigured-even-with-a-stale-connected-row",
    ),
    pytest.param(
        "phone",
        dict(phone_status="not_connected"),
        "Connect a phone number.",
        id="phone-not-connected",
    ),
    pytest.param(
        "phone",
        dict(phone_status="misconfigured"),
        "Your phone number needs attention: test it on the Phone page.",
        id="phone-misconfigured",
    ),
    pytest.param(
        "test_chat",
        dict(test_chat_done=False),
        "Have a test conversation in the test chat.",
        id="test-chat-not-done",
    ),
]


@pytest.mark.parametrize(("key", "changes", "message"), FAILING)
def test_each_failing_variant_fails_only_its_item(key, changes, message):
    readiness = evaluate(replace(ALL_GOOD, **changes))

    failed = [item for item in readiness.items if not item.ok]
    assert [item.key for item in failed] == [key]
    assert failed[0].message == message
    assert failed[0].fix_url == FIX_URLS[key]
    assert readiness.ready is False  # every item is required today
    assert readiness.failing == (failed[0],)


def test_nothing_set_fails_every_item_and_names_them_all():
    nothing = ReadinessFacts(
        agent_name="",
        agent_greeting="",
        active_services=0,
        hours_set=False,
        calendar_configured=True,
        calendar_status="not_connected",
        escalation_contact=False,
        phone_configured=True,
        phone_status="not_connected",
        test_chat_done=False,
    )

    readiness = evaluate(nothing)

    assert not readiness.ready
    assert [item.key for item in readiness.failing] == KEYS


def test_an_advisory_item_does_not_block_ready():
    # `required` is a flag per item so a future advisory item can fail without blocking.
    good = evaluate(ALL_GOOD)
    advisory = ReadinessItem("extra", ok=False, required=False, message="m", fix_url="/x")
    assert Readiness((*good.items, advisory)).ready is True
    required = ReadinessItem("extra", ok=False, required=True, message="m", fix_url="/x")
    assert Readiness((*good.items, required)).ready is False


async def test_the_checker_evaluates_the_facts_of_the_tenants_business():
    tenant = _tenant()
    asked: list[uuid.UUID] = []

    async def facts(business_id: uuid.UUID) -> ReadinessFacts:
        asked.append(business_id)
        return ALL_GOOD

    readiness = await ReadinessChecker(facts).check(tenant)

    assert readiness.ready
    assert tenant.business is not None
    assert asked == [tenant.business.id]


async def test_the_checker_needs_a_business():
    async def facts(business_id: uuid.UUID) -> ReadinessFacts:
        return ALL_GOOD

    no_business = Tenant(user=_tenant().user, business=None)
    with pytest.raises(BusinessNotFoundError):
        await ReadinessChecker(facts).check(no_business)


# --- AgentActivation ---------------------------------------------------------------------


def _tenant() -> Tenant:
    user = User(id=uuid.uuid4(), external_auth_id="user_x", email="owner@example.com")
    business = Business(
        id=uuid.uuid4(),
        owner_id=user.id,
        name="Alpha Salon",
        industry="Salon",
        address="1 Main St",
        timezone="UTC",
    )
    return Tenant(user=user, business=business)


@dataclass
class MemoryAgentStore:
    agents: dict[uuid.UUID, Agent]
    saves: int = 0

    async def get_or_create(
        self, business_id: uuid.UUID, *, name: str, tone: str, greeting: str
    ) -> Agent:
        if business_id not in self.agents:
            self.agents[business_id] = Agent(
                id=uuid.uuid4(),
                business_id=business_id,
                name=name,
                tone=tone,
                greeting=greeting,
                active=False,  # the column default
            )
        return self.agents[business_id]

    async def save(self, agent: Agent) -> Agent:
        self.saves += 1
        return agent


def _activation(facts: ReadinessFacts) -> tuple[AgentActivation, MemoryAgentStore]:
    store = MemoryAgentStore({})

    async def gather(business_id: uuid.UUID) -> ReadinessFacts:
        return facts

    return AgentActivation(store, ReadinessChecker(gather)), store


async def test_activate_flips_active_when_ready_and_is_idempotent():
    tenant = _tenant()
    activation, store = _activation(ALL_GOOD)

    first = await activation.activate(tenant)
    second = await activation.activate(tenant)

    assert first.active and first.readiness.ready
    assert second.active
    assert store.saves == 1  # the second call changed nothing
    assert (await activation.state(tenant)).active is True


async def test_activate_is_blocked_naming_every_failing_item():
    tenant = _tenant()
    activation, store = _activation(
        replace(ALL_GOOD, hours_set=False, calendar_status="reconnect_needed", test_chat_done=False)
    )

    with pytest.raises(AgentNotReadyError) as raised:
        await activation.activate(tenant)

    assert raised.value.status_code == 409 and raised.value.code == "agent_not_ready"
    assert raised.value.message == (
        "Your receptionist isn't ready to take calls yet. "
        "Still needed: opening hours, Google Calendar and a test conversation."
    )
    assert store.saves == 0
    assert (await activation.state(tenant)).active is False


async def test_a_single_missing_item_is_named_alone():
    activation, _ = _activation(replace(ALL_GOOD, active_services=0))

    with pytest.raises(AgentNotReadyError) as raised:
        await activation.activate(_tenant())

    assert raised.value.message.endswith("Still needed: an active service.")


async def test_deactivate_always_works_and_is_idempotent():
    tenant = _tenant()
    # Not ready at all: deactivating must still work.
    activation, store = _activation(replace(ALL_GOOD, hours_set=False))
    assert tenant.business is not None
    agent = await store.get_or_create(
        tenant.business.id, name="Riley", tone="friendly", greeting="Hi"
    )
    agent.active = True

    first = await activation.deactivate(tenant)
    second = await activation.deactivate(tenant)

    assert first.active is False and second.active is False
    assert store.saves == 1
    assert first.readiness.ready is False
