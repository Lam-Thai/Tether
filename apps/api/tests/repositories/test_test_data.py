"""Deleting a business's test data on real Postgres: exactly its own rows go (another
business's stay), and everything that is configuration, today's usage counters and cached
fixed-line audio survives."""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text

from core.db import async_session_factory
from core.readiness import SqlTestDataStore
from models.agent import Agent
from models.booking import Booking
from models.calendar import CalendarConnection
from models.escalation import Escalation, EscalationSettingsRow
from models.faq import Faq
from models.hours import BusinessHours
from models.media import MediaObject, MediaToken
from models.operation import InteractionOperation
from models.phone_number import PhoneNumber
from models.service import Service
from models.usage import UsageCounter, UsageEvent
from models.voice_turn import VoiceTurn
from tests.api.inbox_factory import add_interaction

TODAY = date(2031, 6, 15)
YESTERDAY = date(2031, 6, 14)

TABLES = [
    "interactions",
    "interaction_messages",
    "interaction_operations",
    "escalations",
    "voice_turns",
    "bookings",
    "usage_events",
    "usage_counters",
    "media_objects",
    "media_tokens",
    "agents",
    "services",
    "faqs",
    "business_hours",
    "escalation_settings",
    "calendar_connections",
    "phone_numbers",
]


async def _counts(business_id: uuid.UUID) -> dict[str, int]:
    async with async_session_factory() as session:
        return {
            table: int(
                await session.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE business_id = :b"),  # noqa: S608
                    {"b": business_id},
                )
                or 0
            )
            for table in TABLES
        }


async def _seed(business_id: uuid.UUID) -> uuid.UUID:
    """Test data (2 interactions of 2 messages, an operation, a voice turn, 2 escalations,
    2 bookings, 3 usage events, counters for yesterday and today, reply audio) plus all the
    configuration that must survive. Returns an interaction id."""
    first = await add_interaction(business_id)
    second = await add_interaction(business_id, channel="voice")
    start = datetime(2031, 8, 1, 15, tzinfo=UTC)
    async with async_session_factory() as session:
        session.add_all(
            [
                InteractionOperation(
                    id=uuid.uuid4(),
                    business_id=business_id,
                    interaction_id=first,
                    tool_name="get_services",
                    arguments={},
                    status="succeeded",
                    result={},
                    duration_ms=5,
                ),
                VoiceTurn(
                    id=uuid.uuid4(),
                    business_id=business_id,
                    interaction_id=second,
                    turn=1,
                    status="answered",
                    claim=uuid.uuid4(),
                    heartbeat_at=start,
                ),
            ]
        )
        for reason, interaction in (("urgent", first), ("complaint", second)):
            session.add(
                Escalation(
                    id=uuid.uuid4(),
                    business_id=business_id,
                    interaction_id=interaction,
                    reason=reason,
                    urgency="normal",
                    status="open",
                    sms_status="disabled",
                )
            )
        for hour, status in ((15, "confirmed"), (17, "cancelled")):
            booked = start.replace(hour=hour)
            session.add(
                Booking(
                    id=uuid.uuid4(),
                    business_id=business_id,
                    interaction_id=first,
                    service_name="Haircut",
                    timezone="UTC",
                    calendar_event_id="evt" if status == "confirmed" else None,
                    customer_name="Alex Synthetic",
                    start_time=booked,
                    end_time=booked + timedelta(minutes=45),
                    blocked_until=booked + timedelta(minutes=45),
                    status=status,
                    idempotency_key=uuid.uuid4().hex,
                )
            )
        session.add_all(
            UsageEvent(
                id=uuid.uuid4(),
                business_id=business_id,
                interaction_id=first,
                provider="gemini",
                event_type="gemini_request",
                units=1,
            )
            for _ in range(3)
        )
        session.add_all(
            [
                UsageCounter(
                    business_id=business_id, kind="gemini_request", window_date=day, count=4
                )
                for day in (YESTERDAY, TODAY)
            ]
        )
        # Reply audio goes; a cached fixed line stays (with its token).
        expires = datetime(2031, 6, 16, tzinfo=UTC)
        reply = MediaObject(
            id=uuid.uuid4(),
            business_id=business_id,
            content_type="audio/wav",
            data=b"x",
            expires_at=expires,
        )
        cached = MediaObject(
            id=uuid.uuid4(),
            business_id=business_id,
            content_type="audio/wav",
            data=b"y",
            cache_key="greeting",
            expires_at=expires,
        )
        session.add_all([reply, cached])
        await session.flush()
        session.add_all(
            MediaToken(
                token_hash=uuid.uuid4().bytes + uuid.uuid4().bytes,
                object_id=obj.id,
                business_id=business_id,
                expires_at=expires,
            )
            for obj in (reply, cached)
        )
        # Configuration.
        session.add_all(
            [
                Agent(
                    id=uuid.uuid4(),
                    business_id=business_id,
                    name="Riley",
                    tone="friendly",
                    greeting="Hi",
                    active=True,
                ),
                Service(
                    id=uuid.uuid4(), business_id=business_id, name="Haircut", duration_minutes=45
                ),
                Faq(id=uuid.uuid4(), business_id=business_id, question="Q?", answer="A."),
                BusinessHours(business_id=business_id, day_of_week=0, is_closed=True),
                EscalationSettingsRow(business_id=business_id, contact_phone="+15557654321"),
                CalendarConnection(
                    id=uuid.uuid4(),
                    business_id=business_id,
                    encrypted_refresh_token=b"sealed",
                    token_key_id="test",
                    granted_scopes="a b",
                    connection_status="connected",
                    calendar_id="primary",
                ),
                PhoneNumber(
                    id=uuid.uuid4(),
                    business_id=business_id,
                    phone_number="+1" + str(uuid.uuid4().int)[:10],
                    provider_number_sid="PN" + uuid.uuid4().hex,
                    status="connected",
                ),
            ]
        )
        await session.commit()
    return first


async def _erase(business_id: uuid.UUID):
    async with async_session_factory() as session:
        counts = await SqlTestDataStore(session).erase(business_id, keep_usage_from=TODAY)
        await session.commit()
    return counts


async def test_the_test_data_goes_and_the_configuration_stays(two_tenants):
    tenant_a, _ = two_tenants
    await _seed(tenant_a.business_id)
    before = await _counts(tenant_a.business_id)
    assert before["interactions"] == 2 and before["interaction_messages"] == 4

    counts = await _erase(tenant_a.business_id)

    assert (counts.interactions, counts.messages, counts.bookings) == (2, 4, 2)
    assert (counts.escalations, counts.usage_events) == (2, 3)
    # Only the confirmed booking has an event to cancel (the other is `cancelled`).
    assert counts.calendar_event_ids == ("evt",)
    after = await _counts(tenant_a.business_id)
    gone = (
        "interactions interaction_messages interaction_operations escalations voice_turns "
        "bookings usage_events"
    ).split()
    for table in gone:
        assert after[table] == 0, table
    assert after["usage_counters"] == 1  # yesterday's went, today's stays
    assert after["media_objects"] == 1 and after["media_tokens"] == 1  # the cached line
    for table in (
        "agents",
        "services",
        "faqs",
        "business_hours",
        "escalation_settings",
        "calendar_connections",
        "phone_numbers",
    ):
        assert after[table] == before[table] == 1, table


async def test_todays_counters_and_the_cached_line_are_exactly_what_is_kept(two_tenants):
    tenant_a, _ = two_tenants
    await _seed(tenant_a.business_id)

    await _erase(tenant_a.business_id)

    async with async_session_factory() as session:
        days = (
            await session.execute(
                text("SELECT window_date, count FROM usage_counters WHERE business_id = :b"),
                {"b": tenant_a.business_id},
            )
        ).all()
        keys = (
            await session.execute(
                text("SELECT cache_key FROM media_objects WHERE business_id = :b"),
                {"b": tenant_a.business_id},
            )
        ).all()
        agent_active = await session.scalar(
            text("SELECT active FROM agents WHERE business_id = :b"), {"b": tenant_a.business_id}
        )
    assert [(row[0], row[1]) for row in days] == [(TODAY, 4)]
    assert [row[0] for row in keys] == ["greeting"]
    assert agent_active is True  # a live agent stays live


async def test_another_business_is_untouched(two_tenants):
    tenant_a, tenant_b = two_tenants
    await _seed(tenant_a.business_id)
    await _seed(tenant_b.business_id)
    before = await _counts(tenant_b.business_id)

    await _erase(tenant_a.business_id)

    assert await _counts(tenant_b.business_id) == before


async def test_global_usage_counters_are_never_deleted(two_tenants):
    tenant_a, _ = two_tenants
    await _seed(tenant_a.business_id)
    # A global (business_id NULL) counter for a long-past day: not this business's data.
    long_ago = date(2020, 1, 1)
    async with async_session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO usage_counters (business_id, kind, window_date, count) "
                "VALUES (NULL, 'gemini_request', :d, 5) "
                "ON CONFLICT (kind, window_date) WHERE business_id IS NULL DO NOTHING"
            ),
            {"d": long_ago},
        )
        await session.commit()

    await _erase(tenant_a.business_id)

    async with async_session_factory() as session:
        remaining = await session.scalar(
            text(
                "SELECT count(*) FROM usage_counters "
                "WHERE business_id IS NULL AND window_date = :d AND kind = 'gemini_request'"
            ),
            {"d": long_ago},
        )
        await session.execute(
            text(
                "DELETE FROM usage_counters WHERE business_id IS NULL "
                "AND window_date = :d AND kind = 'gemini_request'"
            ),
            {"d": long_ago},
        )
        await session.commit()
    assert remaining == 1


async def test_the_event_ids_to_cancel_are_this_businesss_confirmed_and_unreconciled_bookings(
    two_tenants,
):
    tenant_a, tenant_b = two_tenants
    start = datetime(2031, 8, 1, 9, tzinfo=UTC)
    rows = [
        # (business, status, event id)
        (tenant_a, "confirmed", "a-confirmed"),
        (tenant_a, "needs_reconciliation", "a-reconcile"),
        (tenant_a, "cancelled", "a-cancelled"),
        (tenant_a, "failed", None),
        (tenant_a, "pending", None),
        (tenant_b, "confirmed", "b-confirmed"),
    ]
    async with async_session_factory() as session:
        for index, (tenant, status, event) in enumerate(rows):
            booked = start + timedelta(hours=index)
            session.add(
                Booking(
                    id=uuid.uuid4(),
                    business_id=tenant.business_id,
                    service_name="Haircut",
                    timezone="UTC",
                    calendar_event_id=event,
                    customer_name="Alex Synthetic",
                    start_time=booked,
                    end_time=booked + timedelta(minutes=30),
                    blocked_until=booked + timedelta(minutes=30),
                    status=status,
                    idempotency_key=uuid.uuid4().hex,
                    created_at=booked,
                )
            )
        await session.commit()

    async with async_session_factory() as session:
        ids = await SqlTestDataStore(session).calendar_event_ids(tenant_a.business_id)

    assert ids == ["a-confirmed", "a-reconcile"]


async def test_the_erase_transaction_gets_a_longer_statement_timeout_than_a_request(two_tenants):
    tenant_a, _ = two_tenants
    async with async_session_factory() as session:
        before = await session.scalar(text("SHOW statement_timeout"))
        await SqlTestDataStore(session).erase(tenant_a.business_id, keep_usage_from=TODAY)
        during = await session.scalar(text("SHOW statement_timeout"))
        await session.commit()
        after = await session.scalar(text("SHOW statement_timeout"))

    assert before == after  # SET LOCAL ends with the transaction
    assert during == "20s" and before != "20s"
