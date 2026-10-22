"""Seeds a synthetic hair salon and its FAQs, for demoing the agent.

    uv run python -m seeds.demo_salon                       # synthetic owner + business
    uv run python -m seeds.demo_salon --owner-email me@x.y  # add the FAQs to your business

Idempotent: a FAQ whose question already exists for the business is skipped.
Uses the app's database settings. Synthetic data only; never run at startup.
"""

import argparse
import asyncio
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import async_session_factory
from models.business import Business
from models.faq import Faq
from models.user import User

SEED_AUTH_ID = "seed|demo-salon"
SEED_EMAIL = "demo-salon-owner@example.test"
BUSINESS_NAME = "Demo Salon (synthetic)"


@dataclass(frozen=True)
class SeedFaq:
    question: str
    answer: str
    category: str | None
    active: bool = True


WALK_IN_QUESTION = "Do you take walk-ins?"

DEMO_SALON_FAQS: tuple[SeedFaq, ...] = (
    SeedFaq(
        WALK_IN_QUESTION,
        "Yes, we take walk-ins before 3 PM, Monday to Saturday. After 3 PM it's appointments only.",
        "Booking",
    ),
    SeedFaq(
        "How do I book an appointment?",
        "Tell me the service and a day that suits you and I'll find a time. You can also call us.",
        "Booking",
    ),
    SeedFaq(
        "What is your cancellation policy?",
        "Please cancel or reschedule at least 24 hours ahead. Late cancellations may be charged "
        "50% of the service price.",
        "Policies",
    ),
    SeedFaq(
        "What if I am running late?",
        "Let us know as soon as you can. We hold your slot for 15 minutes, after that we may "
        "need to rebook you.",
        "Policies",
    ),
    SeedFaq(
        "Do you offer a children's haircut?",
        "Yes, children under 12 get a cut for 18 dollars, about 20 minutes.",
        "Services",
    ),
    SeedFaq(
        "Do you do hair colouring?",
        "Yes: full colour, highlights, balayage and toning. A colour consultation is free.",
        "Services",
    ),
    SeedFaq(
        "Do you do beard trims?",
        "Yes, a beard trim and shape-up takes about 15 minutes.",
        "Services",
    ),
    SeedFaq(
        "How much is a women's haircut?",
        "A women's cut and blow-dry starts at 55 dollars. Final price depends on length and "
        "thickness.",
        "Pricing",
    ),
    SeedFaq(
        "How much is a men's haircut?",
        "A men's cut starts at 30 dollars.",
        "Pricing",
    ),
    SeedFaq(
        "How much does balayage cost?",
        "Balayage starts at 160 dollars and depends on hair length. We confirm the price at the "
        "consultation.",
        "Pricing",
    ),
    SeedFaq(
        "What payment methods do you accept?",
        "We accept cash, all major credit and debit cards, and contactless payments.",
        "Payments",
    ),
    SeedFaq(
        "Do you sell gift cards?",
        "Yes, gift cards in any amount are available at the front desk.",
        "Payments",
    ),
    SeedFaq(
        "Is the salon wheelchair accessible?",
        "Yes, there is a step-free entrance and an accessible restroom.",
        "Accessibility",
    ),
    SeedFaq(
        "Is there parking nearby?",
        "There is free street parking on the block and a paid lot next door.",
        "Location",
    ),
    SeedFaq(
        "Do you offer keratin treatments?",
        "We no longer offer keratin treatments.",
        "Services",
        active=False,
    ),
)


async def _ensure_demo_owner_business(session: AsyncSession) -> Business:
    user = (
        await session.execute(select(User).where(User.external_auth_id == SEED_AUTH_ID))
    ).scalar_one_or_none()
    if user is None:
        user = User(external_auth_id=SEED_AUTH_ID, email=SEED_EMAIL)
        session.add(user)
        await session.flush()
    business = (
        (
            await session.execute(
                select(Business).where(Business.owner_id == user.id).order_by(Business.created_at)
            )
        )
        .scalars()
        .first()
    )
    if business is None:
        business = Business(
            owner_id=user.id,
            name=BUSINESS_NAME,
            industry="Hair salon",
            address="100 Example Street, Springfield",
            timezone="America/New_York",
            phone="+15555550100",
            contact_email="hello@demo-salon.example.test",
        )
        session.add(business)
        await session.flush()
    return business


async def _find_owner_business(session: AsyncSession, owner_email: str) -> Business:
    users = (await session.execute(select(User).where(User.email == owner_email))).scalars().all()
    if not users:
        raise SystemExit(f"No user with email {owner_email!r}. Sign in once first.")
    business = (
        (
            await session.execute(
                select(Business)
                .where(Business.owner_id.in_([u.id for u in users]))
                .order_by(Business.created_at)
            )
        )
        .scalars()
        .first()
    )
    if business is None:
        raise SystemExit(f"{owner_email!r} has no business yet. Finish onboarding first.")
    return business


async def add_faqs(session: AsyncSession, business: Business) -> tuple[int, int]:
    """Adds the missing demo FAQs. Returns (added, skipped)."""
    existing = set(
        (await session.execute(select(Faq.question).where(Faq.business_id == business.id)))
        .scalars()
        .all()
    )
    added = 0
    for item in DEMO_SALON_FAQS:
        if item.question in existing:
            continue
        session.add(
            Faq(
                business_id=business.id,
                question=item.question,
                answer=item.answer,
                category=item.category,
                active=item.active,
            )
        )
        added += 1
    await session.flush()
    return added, len(DEMO_SALON_FAQS) - added


async def run(owner_email: str | None) -> None:
    async with async_session_factory() as session:
        if owner_email:
            business = await _find_owner_business(session, owner_email)
        else:
            business = await _ensure_demo_owner_business(session)
        added, skipped = await add_faqs(session, business)
        await session.commit()
        print(f"{business.name} ({business.id}): added {added} FAQ(s), skipped {skipped} existing.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Seed the synthetic demo salon FAQs.")
    parser.add_argument("--owner-email", help="add the FAQs to this existing user's business")
    args = parser.parse_args(argv)
    asyncio.run(run(args.owner_email))  # SystemExit(message) prints to stderr, exit code 1


if __name__ == "__main__":
    main()
