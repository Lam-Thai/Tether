"""The Postgres sandbox against a real database: seeded tenant, production engine, cleanup.

The model is the fake; the usage window is the `usage` fixture's far-future day, so no
real quota counter moves.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
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
from models.calendar import CalendarConnection
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
        assert (
            await _count(
                Interaction,
                Interaction.business_id == business.id,
                Interaction.status == "in_progress",
            )
            == 0
        )
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


FRIDAY = "2026-06-05"


async def _lookup(sandbox, mode: str, date: str = FRIDAY):
    """One scripted turn: the model asks for a haircut on `date`, then replies."""
    llm = FakeLLMClient()
    llm.queue(
        agent_turn(
            "Checking.",
            intent="BOOK_APPOINTMENT",
            tool_calls=[
                {
                    "name": "check_availability",
                    "arguments": {
                        "service": "Haircut",
                        "appointment_date": date,
                        "time_of_day": "afternoon",
                    },
                }
            ],
        ),
        agent_turn("Let me tell you what I found.", intent="BOOK_APPOINTMENT"),
    )
    recorder = Recorder(llm)
    async with sandbox.case(recorder, mode) as env:
        playback = await play(env, recorder, ["anything friday afternoon for a haircut?"])
    (turn,) = playback.turns
    (op,) = [o for o in turn.operations if o.tool_name == "check_availability"]
    return op


@pytest.mark.parametrize(
    ("mode", "date", "expected"),
    [
        # Haircut is 45 min. Friday is open 09-18; the busy week leaves 14:00-15:30 free.
        ("busy", FRIDAY, {"result": "available", "free": 4}),
        ("open", FRIDAY, {"result": "available", "free": 34}),
        ("fully_booked", "2026-06-09", {"result": "no_availability", "reason": "fully_booked"}),
        ("down", FRIDAY, {"result": "calendar_unavailable", "reason": "unavailable"}),
        ("disconnected", FRIDAY, {"result": "calendar_unavailable", "reason": "not_connected"}),
    ],
)
async def test_each_case_gets_its_own_scripted_calendar(usage, mode, date, expected):
    async with postgres_sandbox(SPEC, usage_clock=lambda: usage.day) as sandbox:
        op = await _lookup(sandbox, mode, date)
    assert op.status == "succeeded"
    assert {key: op.result[key] for key in expected} == expected


async def test_the_tenant_has_a_connected_calendar_row_with_a_sealed_token(usage):
    async with postgres_sandbox(SPEC, usage_clock=lambda: usage.day) as sandbox:
        business = sandbox.tenant.business
        assert business is not None
        async with async_session_factory() as session:
            row = (
                await session.execute(
                    select(CalendarConnection).where(CalendarConnection.business_id == business.id)
                )
            ).scalar_one()
        assert row.connection_status == "connected" and row.calendar_id
        assert b"eval-refresh-token" not in bytes(row.encrypted_refresh_token)
        business_id = business.id
    assert await _count(CalendarConnection, CalendarConnection.business_id == business_id) == 0


async def test_cases_running_at_once_do_not_share_busy_time(usage):
    async with postgres_sandbox(SPEC, usage_clock=lambda: usage.day) as sandbox:
        booked, free = await asyncio.gather(
            _lookup(sandbox, "fully_booked", FRIDAY), _lookup(sandbox, "open", FRIDAY)
        )
    assert booked.result["result"] == "no_availability"
    assert free.result["result"] == "available"


async def test_booking_fails_mode_keeps_the_busy_week_but_fails_every_event_insert():
    from evals.calendar_script import REFRESH_TOKEN, scripted_calendar
    from evals.sandbox import EVAL_NOW

    busy = await scripted_calendar("busy", EVAL_NOW)
    failing = await scripted_calendar("booking_fails", EVAL_NOW)
    assert failing._failures["insert_event"]  # pyright: ignore[reportPrivateUsage]
    assert not busy._failures["insert_event"]  # pyright: ignore[reportPrivateUsage]
    same = failing._of(REFRESH_TOKEN).busy == busy._of(REFRESH_TOKEN).busy  # pyright: ignore
    assert same


async def test_a_replayed_summary_case_reseeds_its_confirmed_booking(usage):
    """A rate-limited summary is replayed by the runner: seeding the same stored case again
    replaces the first seed's booking instead of tripping the double-booking guard, and the
    replay's valid summary is the one stored."""
    from evals.cases import load_suite
    from evals.playback import play_summary
    from evals.scorers import SCORERS
    from integrations.llm.fake import summary_reply
    from integrations.llm.port import LLMUnavailableError
    from models.booking import Booking

    suite = load_suite("summary", known_scorers=frozenset(SCORERS))
    booked = next(c for c in suite.cases if c.stored and c.stored.outcome == "booked")
    assert booked.stored is not None
    llm = FakeLLMClient()
    limited = LLMUnavailableError("429", retryable=False, rate_limited=True)
    sandbox_factory = partial(postgres_sandbox, usage_clock=lambda: usage.day)

    spec = SandboxSpec(max_requests=10, concurrency=1, pacing_allowance_seconds=0)
    async with sandbox_factory(spec) as sandbox:
        business = sandbox.tenant.business
        assert business is not None
        llm.queue(limited)
        first = await play_summary(sandbox, Recorder(llm), booked.stored)
        assert first.turns[0].fallback == "provider" and first.rate_limited_out

        reply = await _booked_summary(booked.stored)
        llm.queue(summary_reply(reply))
        replay = await play_summary(sandbox, Recorder(llm), booked.stored)

        assert replay.turns[0].fallback is None and replay.turns[0].reply == reply
        confirmed = Booking.business_id == business.id, Booking.status == "confirmed"
        assert await _count(Booking, *confirmed) == len(booked.stored.bookings)
        assert await _count(Interaction, Interaction.business_id == business.id) == 1


async def _booked_summary(stored) -> str:
    """A valid two-sentence summary of a stored `booked` case, in the booking's own time."""
    from evals.summary_seed import booking_start

    booking = stored.bookings[0]
    start = booking_start(booking, "America/New_York")
    when = start.strftime("%a %d %b at %I:%M %p").replace(" 0", " ")
    return f"The customer asked to book a {booking.service}. It was booked for {when}."


async def test_summarizes_every_summary_suite_case_on_the_seeded_tenant(usage):
    """Each `summary` case seeds real rows (messages, operation log, bookings) and the
    production Summarizer stores a template summary; the bookings table's double-booking
    guard accepts every confirmed one, and cleanup removes it all."""
    from evals.cases import load_suite
    from evals.playback import play_summary
    from evals.scorers import SCORERS
    from models.booking import Booking

    suite = load_suite("summary", known_scorers=frozenset(SCORERS))
    llm = FakeLLMClient()
    sandbox_factory = partial(postgres_sandbox, usage_clock=lambda: usage.day)

    spec = SandboxSpec(max_requests=40, concurrency=1, pacing_allowance_seconds=0)
    async with sandbox_factory(spec) as sandbox:
        business = sandbox.tenant.business
        assert business is not None
        for case in suite.cases:
            assert case.stored is not None
            llm.queue("not json", "not json")  # invalid twice: the template is stored
            playback = await play_summary(sandbox, Recorder(llm), case.stored)
            (turn,) = playback.turns
            assert turn.fallback == "template", case.id
            scores = [
                SCORERS[name].score(case, playback.turns, playback.operations)
                for name in ("two_sentences", "matches_outcome")
            ]
            assert all(s.passed for s in scores), (case.id, scores)
        assert await _count(Interaction, Interaction.business_id == business.id) == len(suite.cases)
        assert await _count(Booking, Booking.business_id == business.id) >= 3
        business_id = business.id

    assert await _count(Interaction, Interaction.business_id == business_id) == 0
    assert await _count(Booking, Booking.business_id == business_id) == 0


async def test_playback_reads_the_interactions_escalations_from_the_database(usage):
    llm = FakeLLMClient()
    llm.queue(agent_turn("I've let the team know.", intent="HUMAN_REQUEST"))
    sandbox_factory = partial(postgres_sandbox, usage_clock=lambda: usage.day)

    async with sandbox_factory(SPEC) as sandbox:
        recorder = Recorder(llm)
        async with sandbox.case(recorder) as env:
            playback = await play(env, recorder, ["can I talk to a real person please"])

    assert playback.turns[-1].escalations == (("human_requested", "normal"),)
    assert len(llm.requests) == 1
