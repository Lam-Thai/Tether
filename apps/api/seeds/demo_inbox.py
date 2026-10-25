"""Seeds about 200 synthetic interactions (with messages, operation logs and summaries)
into the demo salon, so the owner's inbox has something to filter and page through.

    uv run python -m seeds.demo_inbox                       # the synthetic demo salon
    uv run python -m seeds.demo_inbox --owner-email me@x.y  # your own dev business
    uv run python -m seeds.demo_inbox --count 500

Idempotent: every row carries a stable `external_call_id` (`seed|inbox|<n>`); one that
exists is skipped, so a re-run adds nothing. Synthetic data only: invented first names,
`555` numbers and fixed sentences. Never run at startup.
"""

import argparse
import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import async_session_factory
from models.business import Business
from models.interaction import Interaction, InteractionMessage
from models.operation import InteractionOperation
from seeds.demo_salon import _ensure_demo_owner_business, _find_owner_business

DEFAULT_COUNT = 200
ID_PREFIX = "seed|inbox|"
FIRST_NAMES = ("Alex", "Sam", "Jordan", "Riley", "Casey", "Morgan", "Taylor", "Jamie", "Robin")
IN_PROGRESS_EVERY = 40  # a few conversations are still open


@dataclass(frozen=True)
class Scenario:
    intent: str
    status: str
    outcome: str
    customer: str
    agent: str
    summary: str
    operations: tuple[tuple[str, str, dict], ...] = ()
    requires_human: bool = False
    fallback_reply: bool = False


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "BOOK_APPOINTMENT",
        "completed",
        "booked",
        "I'd like a haircut on Friday afternoon.",
        "You're booked for a haircut on Friday at 3:30 PM.",
        "The customer asked to book a Haircut. The appointment was booked for Fri 30 Oct at "
        "3:30 PM.",
        (
            ("check_availability", "succeeded", {"result": "available", "offered": 3}),
            (
                "create_booking",
                "succeeded",
                {"result": "confirmed", "booking_id": "1a2b3c4d-0000-4000-8000-000000000001"},
            ),
        ),
    ),
    Scenario(
        "FAQ",
        "completed",
        "answered",
        "Do you take walk-ins?",
        "Yes, we take walk-ins before 3 PM, Monday to Saturday.",
        "The customer asked whether walk-ins are welcome. They were told walk-ins are taken "
        "before 3 PM, Monday to Saturday.",
        (("search_faq", "succeeded", {"count": 1}),),
    ),
    Scenario(
        "BUSINESS_HOURS",
        "completed",
        "answered",
        "What time do you close on Saturday?",
        "We close at 4 PM on Saturdays.",
        "The customer asked when the salon closes on Saturday. They were told it closes at 4 PM.",
    ),
    Scenario(
        "PRICE_INQUIRY",
        "completed",
        "answered",
        "How much is a beard trim?",
        "A beard trim is $20 and takes 20 minutes.",
        "The customer asked the price of a beard trim. They were told it is $20 for 20 minutes.",
        (("get_services", "succeeded", {"count": 1}),),
    ),
    Scenario(
        "HUMAN_REQUEST",
        "completed",
        "escalated",
        "Can I talk to a person about a group booking?",
        "I'll flag this for the team. You can also call us directly.",
        "The customer asked to speak to a person about a group booking. The conversation was "
        "flagged for the team.",
        requires_human=True,
    ),
    Scenario(
        "BOOK_APPOINTMENT",
        "failed",
        "failed",
        "Book me a haircut tomorrow at 2.",
        "Sorry, I can't confirm a booking right now. You can reach the team directly.",
        "The customer asked to book a Haircut. The booking could not be completed and nothing "
        "was booked.",
        (
            ("check_availability", "succeeded", {"result": "available", "offered": 2}),
            (
                "create_booking",
                "succeeded",
                {"result": "booking_failed", "reason": "calendar_unavailable"},
            ),
        ),
        requires_human=True,
        fallback_reply=True,
    ),
    Scenario(
        "LOCATION",
        "abandoned",
        "abandoned",
        "Where are you located?",
        "We're at 100 Example Street, Springfield.",
        "The customer asked where the salon is. The conversation ended before they replied.",
    ),
    Scenario(
        "COMPLAINT",
        "completed",
        "escalated",
        "My last colour did not turn out well.",
        "I'm sorry to hear that. I'll flag this for the team to follow up.",
        "The customer complained about a previous colour. The conversation was flagged for "
        "the team.",
        requires_human=True,
    ),
)


def _scenario_for(index: int) -> Scenario:
    return SCENARIOS[(index * 5 + index // 8) % len(SCENARIOS)]


def build_interaction(
    business_id: uuid.UUID, index: int, anchor: datetime
) -> tuple[Interaction, list[InteractionMessage], list[InteractionOperation]]:
    """One synthetic interaction, its messages and its operation log (not yet in a session)."""
    scenario = _scenario_for(index)
    in_progress = index % IN_PROGRESS_EVERY == 0
    started_at = anchor - timedelta(minutes=53 * index + 7)
    ended_at = None if in_progress else started_at + timedelta(minutes=4 + index % 5)
    name = FIRST_NAMES[index % len(FIRST_NAMES)]
    voice = index % 3 == 0
    state: dict = {
        "entities": {"customer_name": name},
        "intents": [scenario.intent],
        "requires_human": scenario.requires_human,
    }
    interaction_id = uuid.uuid4()
    messages = [
        ("agent", "Hi, thanks for contacting Demo Salon! How can I help you today?"),
        ("customer", scenario.customer),
        ("agent", scenario.agent),
    ]
    if scenario.fallback_reply:
        state["fallback_messages"] = [3]
    if index % 4 == 0:
        messages += [("customer", "Thanks, that's all."), ("agent", "Happy to help. Goodbye!")]
    interaction = Interaction(
        id=interaction_id,
        business_id=business_id,
        channel="voice" if voice else "test_chat",
        external_call_id=f"{ID_PREFIX}{index}",
        caller_identifier=f"+1555555{index % 10000:04d}" if voice else None,
        intent=scenario.intent,
        status="in_progress" if in_progress else scenario.status,
        outcome=None if in_progress else scenario.outcome,
        summary=None if in_progress else scenario.summary,
        summary_source=None if in_progress else "template",
        state=state,
        started_at=started_at,
        ended_at=ended_at,
        created_at=started_at,
        updated_at=anchor if in_progress else ended_at,
    )
    rows = [
        InteractionMessage(
            id=uuid.uuid4(),
            interaction_id=interaction_id,
            business_id=business_id,
            speaker=speaker,
            message_text=text,
            sequence_number=number,
            created_at=started_at + timedelta(seconds=20 * number),
        )
        for number, (speaker, text) in enumerate(messages, start=1)
    ]
    operations = [
        InteractionOperation(
            id=uuid.uuid4(),
            business_id=business_id,
            interaction_id=interaction_id,
            tool_name=tool,
            arguments={},
            status=status,
            result=result,
            duration_ms=40 + 15 * position,
            created_at=started_at + timedelta(seconds=30 + 5 * position),
        )
        for position, (tool, status, result) in enumerate(scenario.operations)
    ]
    return interaction, rows, operations


async def add_interactions(
    session: AsyncSession,
    business: Business,
    count: int = DEFAULT_COUNT,
    *,
    anchor: datetime | None = None,
) -> tuple[int, int]:
    """Adds the missing synthetic interactions. Returns (added, skipped)."""
    anchor = anchor or datetime.now(UTC)
    existing = set(
        (
            await session.execute(
                select(Interaction.external_call_id).where(
                    Interaction.business_id == business.id,
                    Interaction.external_call_id.like(f"{ID_PREFIX}%"),
                )
            )
        ).scalars()
    )
    added = 0
    for index in range(count):
        if f"{ID_PREFIX}{index}" in existing:
            continue
        interaction, messages, operations = build_interaction(business.id, index, anchor)
        session.add(interaction)
        session.add_all(messages)
        session.add_all(operations)
        added += 1
    await session.flush()
    return added, count - added


async def run(owner_email: str | None, count: int) -> None:
    async with async_session_factory() as session:
        if owner_email:
            business = await _find_owner_business(session, owner_email)
        else:
            business = await _ensure_demo_owner_business(session)
        added, skipped = await add_interactions(session, business, count)
        await session.commit()
        print(
            f"{business.name} ({business.id}): added {added} interaction(s), "
            f"skipped {skipped} existing."
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Seed synthetic inbox interactions.")
    parser.add_argument("--owner-email", help="add the data to this existing user's business")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    args = parser.parse_args(argv)
    asyncio.run(run(args.owner_email, args.count))


if __name__ == "__main__":
    main()
