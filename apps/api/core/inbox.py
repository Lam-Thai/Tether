"""Builds the owner's `Inbox` for a request.

`SqlInteractionQueries` lives here, not in `repositories/`, because it speaks the
service's dataclasses (`services/inbox`); the SQL itself is in
`repositories/interaction_queries.py`.
"""

import uuid
from collections.abc import Sequence

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_session
from core.escalation import escalation_record
from core.llm import get_llm_client
from core.summaries import build_summarizer
from core.usage import get_usage_guard
from integrations.llm.port import LLMClient
from models.interaction import Interaction
from repositories import escalations as escalations_repo
from repositories import interaction_queries as queries
from repositories.interactions import SqlConversationStore
from services.escalation import EscalationRecord
from services.escalation_policy import REASONS
from services.inbox import (
    Cursor,
    EscalationBrief,
    Inbox,
    InboxFilters,
    InteractionDetail,
    InteractionRow,
    MessageItem,
    MessagePage,
    OperationItem,
    Page,
)
from services.lifecycle import InteractionLifecycle
from services.usage import UsageGuard


def escalation_brief(records: Sequence[EscalationRecord]) -> EscalationBrief | None:
    """The open escalation with the highest-priority reason, else the latest resolved one
    (`records` newest first)."""
    open_ones = [r for r in records if r.status == "open"]
    if open_ones:
        # `min` keeps the first (newest) among equal priorities.
        chosen = min(open_ones, key=lambda r: REASONS.index(r.reason))  # type: ignore[arg-type]
    elif records:
        chosen = records[0]
    else:
        return None
    return EscalationBrief(
        chosen.id, chosen.reason, chosen.urgency, chosen.status, chosen.sms_status
    )


def interaction_row(
    interaction: Interaction,
    has_booking: bool = False,
    escalations: Sequence[EscalationRecord] = (),
) -> InteractionRow:
    state = interaction.state or {}
    entities = state.get("entities")
    name = entities.get("customer_name") if isinstance(entities, dict) else None
    return InteractionRow(
        id=interaction.id,
        channel=interaction.channel,
        started_at=interaction.started_at,
        ended_at=interaction.ended_at,
        caller=interaction.caller_identifier or (name if isinstance(name, str) and name else None),
        intent=interaction.intent,
        summary=interaction.summary,
        summary_source=interaction.summary_source,
        status=interaction.status,
        # A booking confirmed after the conversation closed still makes it a booked one.
        outcome="booked"
        if has_booking and interaction.status != "in_progress"
        else interaction.outcome,
        requires_human=state.get("requires_human") is True,
        needs_attention=any(e.status == "open" for e in escalations),
        escalation=escalation_brief(escalations),
    )


async def _escalations(
    session: AsyncSession, business_id: uuid.UUID, interaction_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[EscalationRecord]]:
    rows = await escalations_repo.list_for_interactions(session, business_id, interaction_ids)
    grouped: dict[uuid.UUID, list[EscalationRecord]] = {}
    for row in rows:
        grouped.setdefault(row.interaction_id, []).append(escalation_record(row))
    return grouped


class SqlInteractionQueries:
    """Binds `repositories/interaction_queries` to one session
    (`services.inbox.InteractionQueries`)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(
        self, business_id: uuid.UUID, filters: InboxFilters, cursor: Cursor | None, limit: int
    ) -> Page[InteractionRow]:
        found = list(
            await queries.list_interactions(
                self._session,
                business_id,
                kind=filters.type,
                status=filters.status,
                intent=filters.intent,
                starts_at=filters.starts_at,
                ends_before=filters.ends_before,
                after=(cursor.started_at, cursor.id) if cursor else None,
                limit=limit + 1,  # one more says whether there is a next page
            )
        )
        more = len(found) > limit
        found = found[:limit]
        next_cursor = Cursor(found[-1][0].started_at, found[-1][0].id) if more else None
        # One extra query for the whole page (no N+1).
        by_interaction = await _escalations(self._session, business_id, [i.id for i, _ in found])
        return Page(
            tuple(interaction_row(i, booked, by_interaction.get(i.id, ())) for i, booked in found),
            next_cursor,
        )

    async def detail(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID
    ) -> InteractionDetail | None:
        found = await queries.get_interaction_for_inbox(self._session, business_id, interaction_id)
        if found is None:
            return None
        interaction, has_booking = found
        count = await queries.count_messages(self._session, business_id, interaction_id)
        operations = await queries.list_operation_rows(self._session, business_id, interaction_id)
        escalations = (await _escalations(self._session, business_id, [interaction_id])).get(
            interaction_id, []
        )
        return InteractionDetail(
            row=interaction_row(interaction, has_booking, escalations),
            escalations=tuple(escalations),
            message_count=count,
            operations=tuple(
                OperationItem(
                    id=op.id,
                    tool_name=op.tool_name,
                    status=op.status,
                    created_at=op.created_at,
                    duration_ms=op.duration_ms,
                    result=dict(op.result or {}),
                )
                for op in operations
            ),
        )

    async def messages(
        self, business_id: uuid.UUID, interaction_id: uuid.UUID, after: int, limit: int
    ) -> MessagePage | None:
        found = await queries.get_interaction_for_inbox(self._session, business_id, interaction_id)
        if found is None:
            return None
        interaction = found[0]
        fallbacks = set((interaction.state or {}).get("fallback_messages", []))
        rows = list(
            await queries.list_messages_after(
                self._session, business_id, interaction_id, after, limit + 1
            )
        )
        more = len(rows) > limit
        rows = rows[:limit]
        return MessagePage(
            tuple(
                MessageItem(
                    sequence_number=m.sequence_number,
                    speaker=m.speaker,
                    text=m.message_text,
                    created_at=m.created_at,
                    fallback=m.speaker == "agent" and m.sequence_number in fallbacks,
                )
                for m in rows
            ),
            rows[-1].sequence_number if more else None,
        )


def build_inbox(session: AsyncSession, llm: LLMClient, usage: UsageGuard) -> Inbox:
    summarizer = build_summarizer(session, llm, usage)
    lifecycle = InteractionLifecycle(
        SqlConversationStore(session), summarizer, idle_minutes=usage.interaction_idle_minutes
    )
    return Inbox(SqlInteractionQueries(session), lifecycle)


def get_inbox(
    session: AsyncSession = Depends(get_session),
    llm: LLMClient = Depends(get_llm_client),
    usage: UsageGuard = Depends(get_usage_guard),
) -> Inbox:
    return build_inbox(session, llm, usage)
