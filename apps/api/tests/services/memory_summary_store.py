"""In-memory `SummaryStore` with the SQL store's claim/save rules (no DB)."""

import uuid
from dataclasses import replace
from datetime import datetime, timedelta

from services.summarizer import InteractionFacts, Summary


class MemorySummaryStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[uuid.UUID, uuid.UUID], InteractionFacts] = {}
        self.claimed_at: dict[tuple[uuid.UUID, uuid.UUID], datetime] = {}
        self.checkpoints = 0

    def add(self, business_id: uuid.UUID, facts: InteractionFacts) -> uuid.UUID:
        interaction_id = uuid.uuid4()
        self.rows[(business_id, interaction_id)] = facts
        return interaction_id

    async def load(self, business_id: uuid.UUID, interaction_id: uuid.UUID):
        return self.rows.get((business_id, interaction_id))

    async def claim(
        self,
        business_id: uuid.UUID,
        interaction_id: uuid.UUID,
        *,
        now: datetime,
        lease_seconds: int,
    ) -> bool:
        key = (business_id, interaction_id)
        facts = self.rows.get(key)
        if facts is None or facts.status == "in_progress" or facts.summary:
            return False
        claimed = self.claimed_at.get(key)
        if claimed is not None and claimed > now - timedelta(seconds=lease_seconds):
            return False
        self.claimed_at[key] = now
        return True

    async def save(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID, summary: Summary
    ) -> bool:
        key = (business_id, interaction_id)
        facts = self.rows.get(key)
        if facts is None or facts.status == "in_progress" or facts.summary:
            return False
        self.rows[key] = replace(facts, summary=summary.text, summary_source=summary.source)
        return True

    async def checkpoint(self) -> None:
        self.checkpoints += 1
