"""In-memory `ConversationStore` (conversation, agent and operation-log operations),
same contract as the SQL stores and scoped by business id like them, for unit tests."""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from models.agent import Agent
from models.interaction import Interaction, InteractionMessage

NOW = datetime(2026, 10, 20, tzinfo=UTC)


class MemoryStore:
    """Same contract as `SqlConversationStore`, scoped by business id like the real one."""

    def __init__(self) -> None:
        self.agents: dict[uuid.UUID, Agent] = {}
        self.interactions: dict[uuid.UUID, Interaction] = {}
        self.messages: list[InteractionMessage] = []
        self.operations: list[dict] = []
        self.calls: list[str] = []
        # What the sweep treats as "now", and each interaction's booking facts
        # (confirmed booking, booking_failed op) that the SQL store reads from other tables.
        self.now = NOW
        self.signals: dict[uuid.UUID, tuple[bool, bool]] = {}

    async def get_or_create(self, business_id, *, name, tone, greeting):
        if business_id not in self.agents:
            self.agents[business_id] = Agent(
                id=uuid.uuid4(),
                business_id=business_id,
                name=name,
                tone=tone,
                greeting=greeting,
                custom_instructions="",
                active=True,
            )
        return self.agents[business_id]

    async def save(self, agent):
        return agent

    async def create_interaction(self, business_id, channel):
        interaction = Interaction(
            id=uuid.uuid4(),
            business_id=business_id,
            channel=channel,
            status="in_progress",
            state={},
            started_at=NOW,
            updated_at=NOW,
            ended_at=None,
        )
        self.interactions[interaction.id] = interaction
        return interaction

    async def lock_admission_and_count(self, business_id, idle_minutes):
        self.calls.append("admission")
        active = [i for i in self.interactions.values() if i.status == "in_progress"]
        mine = sum(1 for i in active if i.business_id == business_id)
        return len(active), mine

    async def get_interaction(self, business_id, interaction_id, channel):
        found = self.interactions.get(interaction_id)
        if found and found.business_id == business_id and found.channel == channel:
            return found
        return None

    lock_interaction = get_interaction

    async def append_message(self, interaction, speaker, message_text):
        number = 1 + sum(1 for m in self.messages if m.interaction_id == interaction.id)
        message = InteractionMessage(
            id=uuid.uuid4(),
            interaction_id=interaction.id,
            business_id=interaction.business_id,
            speaker=speaker,
            message_text=message_text,
            sequence_number=number,
            created_at=NOW,
        )
        self.messages.append(message)
        return message

    async def list_messages(self, business_id, interaction_id, limit=None):
        rows = [
            m
            for m in self.messages
            if m.business_id == business_id and m.interaction_id == interaction_id
        ]
        return rows[-limit:] if limit else rows

    async def save_interaction(self, interaction):
        return interaction

    async def closing_signals(self, business_id, interaction_id):
        return self.signals.get(interaction_id, (False, False))

    async def lock_idle(self, business_id, idle_minutes, limit):
        cutoff = self.now - timedelta(minutes=idle_minutes)
        idle = [
            i
            for i in self.interactions.values()
            if i.business_id == business_id and i.status == "in_progress" and i.updated_at < cutoff
        ]
        return sorted(idle, key=lambda i: i.updated_at)[:limit]

    async def rollback(self):
        self.calls.append("rollback")

    async def checkpoint(self):
        self.calls.append("checkpoint")

    @asynccontextmanager
    async def savepoint(self):
        self.calls.append("savepoint")
        yield

    async def record_operation(self, **row):
        self.calls.append("record_operation")
        self.operations.append(row)
