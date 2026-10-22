"""Seeds a synthetic hair salon with FAQs, services and weekly hours, for demoing the agent.

    uv run python -m seeds.demo_salon                       # synthetic owner + business
    uv run python -m seeds.demo_salon --owner-email me@x.y  # add them to your business

Idempotent: a FAQ whose question exists is skipped, a service whose name exists
(ignoring case) is skipped, and hours are written only if the business has none.
Uses the app's database settings. Synthetic data only; never run at startup.
"""

import argparse
import asyncio
import uuid
from dataclasses import dataclass
from datetime import time
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import async_session_factory
from models.business import Business
from models.faq import Faq
from models.hours import BusinessHours
from models.service import Service
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


@dataclass(frozen=True)
class SeedService:
    name: str
    duration_minutes: int
    price: Decimal | None
    description: str | None = None
    active: bool = True


# "Haircut" and "Kids cut" make "cut" ambiguous on purpose; there is no perm at all, so
# "How much is a perm?" is a "we don't list that"; "Hot towel shave" is inactive (never offered).
DEMO_SALON_SERVICES: tuple[SeedService, ...] = (
    SeedService("Haircut", 45, Decimal("40.00"), "Cut and style, wash included."),
    SeedService("Kids cut", 20, Decimal("18.00"), "For children under 12."),
    SeedService("Beard trim", 15, Decimal("15.00"), "Trim and shape-up."),
    SeedService("Full colour", 120, Decimal("95.00"), "Single-process colour."),
    SeedService("Balayage", 180, Decimal("160.00"), "Price depends on hair length."),
    SeedService("Blow-dry", 30, Decimal("25.00")),
    SeedService("Colour consultation", 20, None, "Free; ask us about pricing."),
    SeedService("Hot towel shave", 30, Decimal("30.00"), "Retired from the menu.", active=False),
)


@dataclass(frozen=True)
class SeedDay:
    day_of_week: int  # 0 = Monday
    opens_at: time | None
    closes_at: time | None

    @property
    def is_closed(self) -> bool:
        return self.opens_at is None


# Local time in the business's timezone: closed Sunday and Monday, shorter Saturday.
DEMO_SALON_HOURS: tuple[SeedDay, ...] = (
    SeedDay(0, None, None),
    SeedDay(1, time(9), time(18)),
    SeedDay(2, time(9), time(18)),
    SeedDay(3, time(9), time(20)),
    SeedDay(4, time(9), time(18)),
    SeedDay(5, time(10), time(16)),
    SeedDay(6, None, None),
)


def new_demo_business(owner_id: uuid.UUID) -> Business:
    """The synthetic salon's business row (not yet added to a session)."""
    return Business(
        owner_id=owner_id,
        name=BUSINESS_NAME,
        industry="Hair salon",
        address="100 Example Street, Springfield",
        timezone="America/New_York",
        currency="USD",
        phone="+15555550100",
        contact_email="hello@demo-salon.example.test",
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
        business = new_demo_business(user.id)
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


async def add_services(session: AsyncSession, business: Business) -> tuple[int, int]:
    """Adds the missing demo services (names compared ignoring case). Returns (added, skipped)."""
    existing = set(
        (
            await session.execute(
                select(func.lower(Service.name)).where(Service.business_id == business.id)
            )
        )
        .scalars()
        .all()
    )
    added = 0
    for item in DEMO_SALON_SERVICES:
        if item.name.lower() in existing:
            continue
        session.add(
            Service(
                business_id=business.id,
                name=item.name,
                description=item.description,
                duration_minutes=item.duration_minutes,
                price=item.price,
                active=item.active,
            )
        )
        added += 1
    await session.flush()
    return added, len(DEMO_SALON_SERVICES) - added


async def set_hours_if_missing(session: AsyncSession, business: Business) -> bool:
    """Writes the demo week only when the business has no hours. Returns whether it did."""
    stored = (
        await session.execute(
            select(func.count())
            .select_from(BusinessHours)
            .where(BusinessHours.business_id == business.id)
        )
    ).scalar_one()
    if stored:
        return False
    session.add_all(
        BusinessHours(
            business_id=business.id,
            day_of_week=day.day_of_week,
            is_closed=day.is_closed,
            opens_at=day.opens_at,
            closes_at=day.closes_at,
        )
        for day in DEMO_SALON_HOURS
    )
    await session.flush()
    return True


async def run(owner_email: str | None) -> None:
    async with async_session_factory() as session:
        if owner_email:
            business = await _find_owner_business(session, owner_email)
        else:
            business = await _ensure_demo_owner_business(session)
        added, skipped = await add_faqs(session, business)
        services_added, services_skipped = await add_services(session, business)
        hours_set = await set_hours_if_missing(session, business)
        await session.commit()
        print(
            f"{business.name} ({business.id}): added {added} FAQ(s), skipped {skipped} existing; "
            f"added {services_added} service(s), skipped {services_skipped} existing; "
            f"hours {'set' if hours_set else 'left as they were'}."
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Seed the synthetic demo salon (FAQs, services, hours)."
    )
    parser.add_argument("--owner-email", help="add the demo data to this existing user's business")
    args = parser.parse_args(argv)
    asyncio.run(run(args.owner_email))  # SystemExit(message) prints to stderr, exit code 1


if __name__ == "__main__":
    main()
