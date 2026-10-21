"""The owner's receptionist persona: read it (creating the defaults on first
read) and update it.

Persona changes take effect on the very next conversation turn because
`services.conversation` reloads the agent from the store every turn; there
is no cache to invalidate. No FastAPI or SQLAlchemy imports.
"""

import uuid
from typing import Any, Protocol

from core.tenant import Tenant
from models.agent import Agent
from models.business import Business
from services.business import BusinessNotFoundError

DEFAULT_NAME = "Riley"
DEFAULT_TONE = "friendly"
EDITABLE_FIELDS = frozenset({"name", "tone", "greeting", "custom_instructions", "active"})


class AgentStore(Protocol):
    async def get_or_create(
        self, business_id: uuid.UUID, *, name: str, tone: str, greeting: str
    ) -> Agent: ...
    async def save(self, agent: Agent) -> Agent: ...


def default_greeting(business: Business) -> str:
    return f"Hi, thanks for contacting {business.name}! How can I help you today?"


def require_business(tenant: Tenant) -> Business:
    if tenant.business is None:
        raise BusinessNotFoundError("You haven't created a business yet.")
    return tenant.business


async def load_agent(store: AgentStore, business: Business) -> Agent:
    return await store.get_or_create(
        business.id, name=DEFAULT_NAME, tone=DEFAULT_TONE, greeting=default_greeting(business)
    )


async def get_agent(store: AgentStore, tenant: Tenant) -> Agent:
    return await load_agent(store, require_business(tenant))


async def update_agent(store: AgentStore, tenant: Tenant, **changes: Any) -> Agent:
    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"Not editable: {', '.join(sorted(unknown))}")
    agent = await load_agent(store, require_business(tenant))
    for key, value in changes.items():
        setattr(agent, key, value)
    return await store.save(agent)
