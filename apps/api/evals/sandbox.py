"""The Postgres sandbox: a throwaway synthetic salon tenant for one eval run.

`postgres_sandbox(spec)` creates a fresh owner and business (`eval|<random>`),
seeds it with the demo salon's FAQs, services and hours (`seeds/demo_salon.py`,
synthetic only), and deletes both afterwards, whatever happened; every row
the run wrote (agent, interactions, messages, operations, usage) cascades
with the business. Nothing else in the database is read or changed, apart
from the global usage counters, which keep counting real Gemini calls.

Each case gets its own session, committed after every turn and after the
conversation ends (as the chat API commits each request), and the production
engine and tool registry (`core.conversation.build_conversation_engine`), with:

- a `UsageGuard` over the real usage tables, whose per-business Gemini cap
  is the run's `max_requests` (the tenant is new, so its counter starts at 0)
  and whose per-business concurrency cap fits the run's concurrency;
- a scripted fake Google Calendar per case (`evals/calendar_script.py`): the tenant has
  one real `calendar_connections` row (a fake refresh token sealed in a throwaway
  `TokenVault`), and each case gets its own fake account behind it, so cases running
  at once never share busy time;
- a business clock pinned to `EVAL_NOW`, so "today" and the next 7 days of
  hours in the prompt are the same on every run;
- the per-turn deadline plus the runner's pacing allowance.
"""

import asyncio
import logging
import os
import secrets
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.conversation import CalendarDeps, build_conversation_engine
from core.db import async_session_factory
from core.summaries import build_summarizer
from core.tenant import Tenant
from core.usage import SqlUsageStore, cost_rates, usage_limits
from evals.calendar_script import (
    CALENDAR_ID,
    CALENDAR_NAME,
    REFRESH_TOKEN,
    scripted_calendar,
)
from evals.cases import CalendarMode, StoredInteraction
from evals.playback import CaseEnv, Operation
from evals.runner import SandboxSpec
from evals.summary_seed import BOOKING_MINUTES, booking_start
from integrations.google_calendar.port import REQUIRED_SCOPES
from integrations.llm.port import LLMClient
from models.booking import Booking
from models.business import Business
from models.interaction import Interaction, InteractionMessage
from models.operation import InteractionOperation
from models.user import User
from repositories.calendar import SqlCalendarStore
from repositories.operations import list_operations
from seeds.demo_salon import add_faqs, add_services, new_demo_business, set_hours_if_missing
from services.calendar import CalendarConfig, token_context
from services.oauth_state import OAuthStateSigner
from services.summarizer import Summary
from services.token_vault import TokenVault
from services.usage import DailyLimit, UsageGuard, UsageKind, UsageLimits

# A Wednesday, 14:00 in the salon's timezone (America/New_York): open today.
EVAL_NOW = datetime(2026, 6, 3, 18, 0, tzinfo=UTC)
EVAL_AUTH_PREFIX = "eval|"

logger = logging.getLogger("tether.evals")


def eval_usage_limits(base: UsageLimits, spec: SandboxSpec) -> UsageLimits:
    """The app's limits, with the run's budget as the tenant's Gemini cap.

    The global caps stay as configured: eval calls are real calls and count
    against the same daily quota as everything else.
    """
    daily = dict(base.daily)
    gemini = daily[UsageKind.GEMINI_REQUEST]
    daily[UsageKind.GEMINI_REQUEST] = DailyLimit(
        per_day=gemini.per_day, per_business_per_day=spec.max_requests
    )
    return replace(
        base,
        daily=daily,
        max_concurrent_interactions_per_business=max(
            base.max_concurrent_interactions_per_business, spec.concurrency
        ),
    )


@dataclass(frozen=True)
class _PostgresSandbox:
    tenant: Tenant
    usage: UsageGuard
    spec: SandboxSpec
    vault: TokenVault

    @asynccontextmanager
    async def case(self, llm: LLMClient, calendar: CalendarMode = "busy") -> AsyncIterator[CaseEnv]:
        api = await scripted_calendar(calendar, EVAL_NOW)
        deps = CalendarDeps(
            api,
            self.vault,
            OAuthStateSigner(secrets.token_urlsafe(32), 600),
            # `disconnected`: the feature is not set up, so the lookup answers not_connected.
            CalendarConfig(
                client_configured=calendar != "disconnected",
                redirect_uri="https://eval.example.test/callback",
                status_check_seconds=300,
            ),
        )
        async with async_session_factory() as session:
            engine = build_conversation_engine(
                session,
                llm,
                self.usage,
                clock=lambda: EVAL_NOW,
                extra_turn_seconds=self.spec.pacing_allowance_seconds,
                calendar=deps,
            )
            business_id = self.tenant.business.id if self.tenant.business else None
            assert business_id is not None

            async def operations(interaction_id: uuid.UUID) -> Sequence[Operation]:
                rows = await list_operations(session, business_id, interaction_id)
                return [Operation(r.tool_name, r.status, dict(r.result or {})) for r in rows]

            yield CaseEnv(
                tenant=self.tenant, engine=engine, operations=operations, commit=session.commit
            )

    async def summarize(self, llm: LLMClient, stored: StoredInteraction) -> Summary | None:
        """Seed `stored` as a closed interaction of this tenant, then run the real
        `Summarizer` (production wiring, the run's `UsageGuard`) over it."""
        business = self.tenant.business
        assert business is not None
        async with async_session_factory() as session:
            interaction_id = await seed_stored_interaction(session, business, stored)
            await session.commit()
            summarizer = build_summarizer(
                session,
                llm,
                self.usage,
                timeout_seconds=settings.summary_timeout_seconds
                + self.spec.pacing_allowance_seconds,
            )
            return await summarizer.summarize(business.id, interaction_id)


async def seed_stored_interaction(
    session: AsyncSession, business: Business, stored: StoredInteraction
) -> uuid.UUID:
    """Insert a closed interaction with its messages, operation log and bookings (rows
    only; the caller commits). Synthetic text from the suite file."""
    interaction = Interaction(
        id=uuid.uuid4(),
        business_id=business.id,
        channel="test_chat",
        status=stored.status,
        outcome=stored.outcome,
        intent=stored.intent,
        state={"requires_human": stored.requires_human},
        ended_at=EVAL_NOW,
    )
    session.add(interaction)
    await session.flush()
    for number, message in enumerate(stored.messages, start=1):
        session.add(
            InteractionMessage(
                interaction_id=interaction.id,
                business_id=business.id,
                speaker=message.speaker,
                message_text=message.text,
                sequence_number=number,
            )
        )
    for operation in stored.operations:
        session.add(
            InteractionOperation(
                business_id=business.id,
                interaction_id=interaction.id,
                tool_name=operation.tool,
                arguments={},
                status=operation.status,
                result={"result": operation.result} if operation.result else {},
                duration_ms=5,
            )
        )
    for booking in stored.bookings:
        start = booking_start(booking, business.timezone)
        end = start + timedelta(minutes=BOOKING_MINUTES)
        confirmed = booking.status == "confirmed"
        session.add(
            Booking(
                business_id=business.id,
                interaction_id=interaction.id,
                service_name=booking.service,
                timezone=business.timezone,
                customer_name="Eval Customer",
                start_time=start,
                end_time=end,
                blocked_until=end,
                status=booking.status,
                calendar_event_id="eval-event" if confirmed else None,
                failure_reason=None if confirmed else "calendar_unavailable",
                idempotency_key=uuid.uuid4().hex,
            )
        )
    await session.flush()
    return interaction.id


async def connect_fake_calendar(
    session: AsyncSession, business_id: uuid.UUID, vault: TokenVault
) -> None:
    """The tenant's `calendar_connections` row, as after the owner connected a calendar:
    the fake refresh token sealed for this business, the eval calendar selected."""
    store = SqlCalendarStore(session)
    sealed = vault.encrypt(REFRESH_TOKEN, context=token_context(business_id))
    row = await store.upsert_tokens(
        business_id,
        encrypted_refresh_token=sealed.ciphertext,
        token_key_id=sealed.key_id,
        granted_scopes=" ".join(sorted(REQUIRED_SCOPES)),
        synced_at=EVAL_NOW,
    )
    await store.set_selection(
        business_id,
        calendar_id=CALENDAR_ID,
        calendar_name=CALENDAR_NAME,
        synced_at=EVAL_NOW,
        seen_updated_at=row.updated_at,
    )


@asynccontextmanager
async def postgres_sandbox(
    spec: SandboxSpec, *, usage_clock: Callable[[], datetime] | None = None
) -> AsyncIterator[_PostgresSandbox]:
    """`usage_clock` pins the usage window (tests); real runs count on the real UTC day."""
    # Everything that can fail without touching the database happens first, so once the
    # tenant is committed nothing stands between it and the `finally` that removes it.
    usage = UsageGuard(
        SqlUsageStore(async_session_factory),
        eval_usage_limits(usage_limits(settings), spec),
        cost_rates(settings),
        clock=usage_clock or (lambda: datetime.now(UTC)),
    )
    token = uuid.uuid4().hex
    vault = TokenVault({"eval": os.urandom(32)}, "eval")
    async with async_session_factory() as session:
        user = User(
            external_auth_id=f"{EVAL_AUTH_PREFIX}{token}",
            email=f"eval-{token[:12]}@example.test",
        )
        session.add(user)
        await session.flush()
        business = new_demo_business(user.id)
        session.add(business)
        await session.flush()
        await connect_fake_calendar(session, business.id, vault)
        await add_faqs(session, business)
        await add_services(session, business)
        await set_hours_if_missing(session, business)
        await session.commit()
    try:
        yield _PostgresSandbox(Tenant(user=user, business=business), usage, spec, vault)
    finally:
        try:
            # Shielded: a cancelled run (Ctrl-C, a CI timeout) still removes its tenant.
            await asyncio.shield(_delete_tenants([business.id], [user.id]))
        except Exception:
            # Never mask the run's own error; `tether-eval --purge` removes leftovers.
            logger.warning("eval sandbox cleanup failed", extra={"eval_tenant": token})


async def _delete_tenants(business_ids: Sequence[uuid.UUID], user_ids: Sequence[uuid.UUID]) -> None:
    async with async_session_factory() as session:
        # Businesses first: `owner_id` references the user with no cascade. Everything
        # the run wrote (agent, interactions, messages, operations, usage) cascades.
        await session.execute(delete(Business).where(Business.id.in_(business_ids)))
        await session.execute(delete(User).where(User.id.in_(user_ids)))
        await session.commit()


# Longer than any run can last, so a purge never removes a running eval's tenant.
PURGE_MIN_AGE = timedelta(hours=2)


async def purge_eval_tenants(min_age: timedelta = PURGE_MIN_AGE) -> int:
    """Delete leftover eval tenants (owner `eval|...`, created over `min_age` ago),
    e.g. after a hard kill. Only eval-owned rows match. Returns how many owners
    were removed."""
    cutoff = datetime.now(UTC) - min_age
    async with async_session_factory() as session:
        users = (
            (
                await session.execute(
                    select(User.id).where(
                        User.external_auth_id.startswith(EVAL_AUTH_PREFIX), User.created_at < cutoff
                    )
                )
            )
            .scalars()
            .all()
        )
        businesses = (
            (await session.execute(select(Business.id).where(Business.owner_id.in_(users))))
            .scalars()
            .all()
        )
    if users:
        await _delete_tenants(businesses, users)
    return len(users)
