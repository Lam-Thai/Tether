"""`SqlSummaryStore` + `Summarizer` against real Postgres: stored facts, the claim lease,
never overwriting a summary, and business scoping."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from core.db import async_session_factory
from core.summaries import build_summarizer
from integrations.llm.fake import summary_reply
from models.booking import Booking
from models.interaction import Interaction, InteractionMessage
from models.operation import InteractionOperation
from repositories.summaries import SqlSummaryStore
from services.redaction import redact

START = datetime(2031, 3, 7, 20, 30, tzinfo=UTC)  # Fri 7 Mar 3:30 PM in New York
GOOD = "The customer asked to book a haircut. It was booked for Fri 7 Mar at 3:30 PM."


async def _seed(business_id: uuid.UUID, *, status="completed", booked=True) -> uuid.UUID:
    interaction_id = uuid.uuid4()
    async with async_session_factory() as session:
        session.add(
            Interaction(
                id=interaction_id,
                business_id=business_id,
                channel="test_chat",
                status=status,
                outcome="booked" if booked else "failed",
                intent="BOOK_APPOINTMENT",
                state={},
            )
        )
        await session.flush()
        session.add_all(
            [
                InteractionMessage(
                    interaction_id=interaction_id,
                    business_id=business_id,
                    speaker="customer",
                    message_text="Book me a haircut, card 4111 1111 1111 1111",
                    sequence_number=1,
                ),
                InteractionMessage(
                    interaction_id=interaction_id,
                    business_id=business_id,
                    speaker="agent",
                    message_text="You're all set.",
                    sequence_number=2,
                ),
                InteractionOperation(
                    business_id=business_id,
                    interaction_id=interaction_id,
                    tool_name="create_booking",
                    arguments={},
                    status="succeeded",
                    result={"result": "confirmed" if booked else "booking_failed"},
                    duration_ms=5,
                ),
                Booking(
                    business_id=business_id,
                    interaction_id=interaction_id,
                    service_name="Haircut",
                    timezone="America/New_York",
                    customer_name="Alex Synthetic",
                    start_time=START,
                    end_time=START + timedelta(minutes=45),
                    blocked_until=START + timedelta(minutes=45),
                    status="confirmed" if booked else "failed",
                    calendar_event_id="evt_1" if booked else None,
                    idempotency_key=uuid.uuid4().hex,
                ),
            ]
        )
        await session.commit()
    return interaction_id


async def _stored(interaction_id: uuid.UUID) -> Interaction:
    async with async_session_factory() as session:
        return (
            await session.execute(select(Interaction).where(Interaction.id == interaction_id))
        ).scalar_one()


async def test_store_loads_scoped_facts_and_the_summarizer_saves_once(two_tenants, llm, usage):
    tenant_a, tenant_b = two_tenants
    interaction_id = await _seed(tenant_a.business_id)
    llm.queue(summary_reply(GOOD))

    async with async_session_factory() as session:
        facts = await SqlSummaryStore(session).load(tenant_a.business_id, interaction_id)
        assert facts is not None
        assert [m.speaker for m in facts.messages] == ["customer", "agent"]
        assert [(o.tool_name, o.result_code) for o in facts.operations] == [
            ("create_booking", "confirmed")
        ]
        assert [(b.service_name, b.status) for b in facts.bookings] == [("Haircut", "confirmed")]
        # Another business's id finds nothing, and so cannot summarize or claim it.
        assert await SqlSummaryStore(session).load(tenant_b.business_id, interaction_id) is None
        foreign = build_summarizer(session, llm, usage.guard())
        assert await foreign.summarize(tenant_b.business_id, interaction_id) is None
        assert len(llm.requests) == 0

        summarizer = build_summarizer(session, llm, usage.guard())
        summary = await summarizer.summarize(tenant_a.business_id, interaction_id)
        assert summary is not None and (summary.text, summary.source) == (GOOD, "ai")

    request = llm.requests[0]
    assert "4111" not in request.messages[0].text  # redacted before it reaches the model
    row = await _stored(interaction_id)
    assert (row.summary, row.summary_source) == (GOOD, "ai")

    async with async_session_factory() as session:  # idempotent: no second model call
        again = await build_summarizer(session, llm, usage.guard()).summarize(
            tenant_a.business_id, interaction_id
        )
    assert again is not None and again.text == GOOD
    assert len(llm.requests) == 1
    assert redact("4111 1111 1111 1111") != "4111 1111 1111 1111"


async def test_two_concurrent_calls_on_two_sessions_make_one_model_call(two_tenants, llm, usage):
    tenant_a, _ = two_tenants
    interaction_id = await _seed(tenant_a.business_id)
    llm.queue(summary_reply(GOOD), summary_reply(GOOD))

    async def call():
        async with async_session_factory() as session:
            summarizer = build_summarizer(session, llm, usage.guard())
            return await summarizer.summarize(tenant_a.business_id, interaction_id)

    results = await asyncio.gather(call(), call())

    assert len(llm.requests) == 1
    assert [r.text for r in results if r is not None] and all(
        r is None or r.text == GOOD for r in results
    )
    assert (await _stored(interaction_id)).summary == GOOD


async def test_failed_booking_never_summarized_as_booked_and_in_progress_is_none(
    two_tenants, llm, usage
):
    tenant_a, _ = two_tenants
    failed = await _seed(tenant_a.business_id, status="failed", booked=False)
    live = await _seed(tenant_a.business_id, status="in_progress")
    liar = "The customer asked to book a haircut. The appointment was booked successfully."
    llm.queue(summary_reply(liar), summary_reply(liar))

    async with async_session_factory() as session:
        summarizer = build_summarizer(session, llm, usage.guard())
        summary = await summarizer.summarize(tenant_a.business_id, failed)
        assert summary is not None and summary.source == "template"
        assert "nothing was booked" in summary.text
        assert await summarizer.summarize(tenant_a.business_id, live) is None
        assert await summarizer.summarize(tenant_a.business_id, live, ai=False) is None

    assert (await _stored(failed)).summary_source == "template"
    assert (await _stored(live)).summary is None
