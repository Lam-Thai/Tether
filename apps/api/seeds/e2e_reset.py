"""Resets the E2E test user between Playwright runs (slice 17).

    uv run python -m seeds.e2e_reset --email e2e+clerk_test@example.com

Deletes the business of every user with that email (case-insensitive), which cascades to
everything under it: subscription, agent, FAQs, services, hours, calendar connection,
interactions, bookings, escalations and the business's usage rows. The `users` row stays,
so the same Clerk sign-in simply onboards again. Global usage counters are untouched.

Refuses unless the database is on this machine and ENVIRONMENT is not production: it
deletes a whole business without confirmation. Never run at startup.
"""

import argparse
import asyncio

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.db import async_session_factory
from core.db_hosts import is_local_database
from models.business import Business
from models.usage import UsageCounter, UsageEvent
from models.user import User


def refuse_unless_safe() -> None:
    """Raises SystemExit (message to stderr, exit code 1) for production or a remote DB."""
    if settings.environment == "production":
        raise SystemExit("e2e_reset: refusing to run with ENVIRONMENT=production.")
    if not is_local_database(settings.database_url):
        raise SystemExit("e2e_reset: refusing to run against a database that is not local.")


async def reset_user(session: AsyncSession, email: str) -> int:
    """Deletes the businesses (and their usage rows) of users with `email`; returns how
    many businesses were deleted. The caller commits."""
    owner_ids = select(User.id).where(func.lower(User.email) == email.strip().lower())
    business_ids = list(
        (await session.execute(select(Business.id).where(Business.owner_id.in_(owner_ids))))
        .scalars()
        .all()
    )
    if not business_ids:
        return 0
    # Explicit (they also cascade) so the intent reads in one place.
    await session.execute(delete(UsageEvent).where(UsageEvent.business_id.in_(business_ids)))
    await session.execute(delete(UsageCounter).where(UsageCounter.business_id.in_(business_ids)))
    await session.execute(delete(Business).where(Business.id.in_(business_ids)))
    return len(business_ids)


async def run(email: str) -> int:
    refuse_unless_safe()
    async with async_session_factory() as session:
        deleted = await reset_user(session, email)
        await session.commit()
    return deleted


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Delete the E2E user's business (local DB only).")
    parser.add_argument(
        "--email", required=True, help="the E2E user's email (e.g. a +clerk_test one)"
    )
    args = parser.parse_args(argv)
    deleted = asyncio.run(run(args.email))
    print(f"e2e_reset: deleted {deleted} business(es) for {args.email}")


if __name__ == "__main__":
    main()
