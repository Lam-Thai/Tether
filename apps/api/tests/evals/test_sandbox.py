"""The Postgres sandbox against a real database: seeded tenant, production engine, cleanup.

The model is the fake; the usage window is the `usage` fixture's far-future day, so no
real quota counter moves.
"""

import uuid
from datetime import UTC, datetime, timedelta
from functools import partial

from sqlalchemy import delete, func, select

from core.db import async_session_factory
from evals.playback import Recorder, play
from evals.runner import SandboxSpec
from evals.sandbox import (
    EVAL_AUTH_PREFIX,
    PURGE_MIN_AGE,
    eval_usage_limits,
    postgres_sandbox,
    purge_eval_tenants,
)
from integrations.llm.fake import FakeLLMClient, agent_turn
from models.business import Business
from models.faq import Faq
from models.interaction import Interaction
from models.user import User
from seeds.demo_salon import DEMO_SALON_FAQS, WALK_IN_QUESTION, new_demo_business
from services.usage import UsageKind
from tests.services.memory_usage_store import limits

SPEC = SandboxSpec(max_requests=7, concurrency=6, pacing_allowance_seconds=0)


async def _count(model, *where) -> int:
    query = select(func.count()).select_from(model).where(*where)
    async with async_session_factory() as session:
        return (await session.execute(query)).scalar_one()


async def test_plays_a_case_on_a_seeded_tenant_and_deletes_it_afterwards(usage):
    llm = FakeLLMClient()
    sandbox_factory = partial(postgres_sandbox, usage_clock=lambda: usage.day)

    async with sandbox_factory(SPEC) as sandbox:
        business = sandbox.tenant.business
        assert business is not None
        async with async_session_factory() as session:
            walk_in_id = (
                await session.execute(
                    select(Faq.id).where(
                        Faq.business_id == business.id, Faq.question == WALK_IN_QUESTION
                    )
                )
            ).scalar_one()
        assert await _count(Faq, Faq.business_id == business.id) == len(DEMO_SALON_FAQS)

        llm.queue(
            agent_turn(
                "Checking.",
                intent="FAQ",
                tool_calls=[{"name": "search_faq", "arguments": {"query": "walk-ins"}}],
            ),
            agent_turn("Yes, before 3 PM.", intent="FAQ", sources=[str(walk_in_id)]),
        )
        recorder = Recorder(llm)
        async with sandbox.case(recorder) as env:
            playback = await play(env, recorder, ["can I just walk in?"])

        (turn,) = playback.turns
        assert turn.fallback is None and turn.reply == "Yes, before 3 PM."
        (search,) = turn.operations  # read back from interaction_operations
        assert search.tool_name == "search_faq" and search.status == "succeeded"
        # Only this tenant's FAQs can come back.
        assert str(walk_in_id) in search.result["faq_ids"]
        owned = await _count(
            Faq, Faq.business_id == business.id, Faq.id.in_(search.result["faq_ids"])
        )
        assert owned == len(search.result["faq_ids"])
        # The conversation was ended after the case.
        assert await _count(
            Interaction, Interaction.business_id == business.id, Interaction.status == "active"
        ) == 0
        business_id, user_id = business.id, sandbox.tenant.user.id

    assert await _count(Business, Business.id == business_id) == 0
    assert await _count(User, User.id == user_id) == 0
    assert await _count(Faq, Faq.business_id == business_id) == 0  # cascaded


async def test_the_tenant_is_removed_even_when_the_run_fails(usage):
    business_id = None
    try:
        async with postgres_sandbox(SPEC, usage_clock=lambda: usage.day) as sandbox:
            assert sandbox.tenant.business is not None
            business_id = sandbox.tenant.business.id
            raise RuntimeError("run broke")
    except RuntimeError:
        pass
    assert business_id is not None
    assert await _count(Business, Business.id == business_id) == 0


def test_the_run_budget_caps_the_tenant_but_not_the_global_quota():
    base = limits()
    capped = eval_usage_limits(base, SPEC)
    gemini = capped.daily[UsageKind.GEMINI_REQUEST]
    assert gemini.per_business_per_day == SPEC.max_requests
    assert gemini.per_day == base.daily[UsageKind.GEMINI_REQUEST].per_day
    # Concurrency per business fits the run (never lowered below the app's own cap).
    assert capped.max_concurrent_interactions_per_business == max(
        base.max_concurrent_interactions_per_business, SPEC.concurrency
    )
    assert capped.max_concurrent_interactions == base.max_concurrent_interactions


async def test_purge_removes_only_old_eval_tenants():
    async def eval_tenant(age: timedelta) -> tuple[uuid.UUID, uuid.UUID]:
        token = uuid.uuid4().hex
        created = datetime.now(UTC) - age
        async with async_session_factory() as session:
            user = User(
                external_auth_id=f"{EVAL_AUTH_PREFIX}{token}",
                email=f"eval-{token[:12]}@example.test",
                created_at=created,
            )
            session.add(user)
            await session.flush()
            business = new_demo_business(user.id)
            session.add(business)
            await session.commit()
            return user.id, business.id

    old_user, old_business = await eval_tenant(PURGE_MIN_AGE + timedelta(minutes=5))
    fresh_user, fresh_business = await eval_tenant(timedelta(minutes=1))  # a running eval
    try:
        assert await purge_eval_tenants() >= 1
        assert await _count(User, User.id == old_user) == 0
        assert await _count(Business, Business.id == old_business) == 0
        assert await _count(User, User.id == fresh_user) == 1
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(Business).where(Business.id == fresh_business))
            await session.execute(delete(User).where(User.id == fresh_user))
            await session.commit()
