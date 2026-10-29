"""The owner's receptionist persona: read it (creating the defaults on first
read) and update it.

Persona changes take effect on the very next conversation turn because
`services.conversation` reloads the agent from the store every turn; there
is no cache to invalidate. No FastAPI or SQLAlchemy imports.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from core.tenant import Tenant
from models.agent import Agent
from models.business import Business
from services.business import BusinessNotFoundError
from services.speech import SpeechSynthesizer, UnknownVoiceError, VoiceCatalog

DEFAULT_NAME = "Riley"
DEFAULT_TONE = "friendly"
EDITABLE_FIELDS = frozenset({"name", "tone", "greeting", "custom_instructions", "voice"})


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


async def update_agent(
    store: AgentStore, tenant: Tenant, *, catalog: VoiceCatalog | None = None, **changes: Any
) -> Agent:
    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"Not editable: {', '.join(sorted(unknown))}")
    if "voice" in changes and (catalog is None or not catalog.is_valid(changes["voice"])):
        raise UnknownVoiceError("That voice isn't available.")
    agent = await load_agent(store, require_business(tenant))
    for key, value in changes.items():
        setattr(agent, key, value)
    return await store.save(agent)


async def preview_voice(
    store: AgentStore,
    speech: SpeechSynthesizer,
    tenant: Tenant,
    voice: str,
    *,
    checkpoint: Callable[[], Awaitable[None]],
) -> bytes:
    """The saved greeting in `voice`, as callers would hear it (a metered TTS request
    unless the greeting is already cached for that voice). `checkpoint` commits the
    request's session after the read (which may create the default agent), so no
    transaction is held across the provider call."""
    business = require_business(tenant)
    agent = await load_agent(store, business)
    greeting, business_id = agent.greeting, business.id
    await checkpoint()
    return await speech.preview(greeting, voice, business_id=business_id)
