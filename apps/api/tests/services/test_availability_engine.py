"""`check_availability` through the conversation engine: scripted fake model, in-memory
stores, a scripted busy source for the calendar. No DB, no Google, no Gemini.

Covers the slice's safety property end to end: the agent never states a time the
calendar and the hours don't support (the time guard), a calendar outage gets an
honest reply with no times, and offered slots land in `interactions.state`.
"""

import uuid
from datetime import UTC, date, datetime

import pytest

from core.retry import RetryPolicy
from core.tenant import Tenant
from integrations.llm.fake import FakeLLMClient, agent_turn
from models.business import Business
from models.user import User
from services.availability import AvailabilityService
from services.availability_tool import CHECK_AVAILABILITY, OFFERED_SLOTS, check_availability_tool
from services.booking_rules import BookingRules
from services.business_tools import get_business_info_tool, get_services_tool
from services.calendar import CalendarNotSelectedError, CalendarUnavailableError
from services.conversation import TIME_CORRECTION, ConversationEngine, availability_fallback
from services.directory import BusinessDirectory
from services.tools import ToolRegistry
from tests.services.fake_busy_source import FakeBusySource
from tests.services.memory_conversation_store import MemoryStore
from tests.services.memory_directory_store import MemoryDirectoryStore
from tests.services.memory_usage_store import guard

# Wednesday 2026-10-07, 14:00 in New York.
NOW = datetime(2026, 10, 7, 18, 0, tzinfo=UTC)
FRIDAY = date(2026, 10, 9)
WEEK = {
    0: None,
    1: ("09:00", "17:00"),
    2: ("09:00", "17:00"),
    3: ("09:00", "17:00"),
    4: ("09:00", "17:00"),
    5: ("10:00", "13:00"),
    6: None,
}
RULES = BookingRules(
    slot_interval_minutes=15, min_notice_minutes=120, max_days_ahead=60, buffer_minutes=0
)


def _tenant() -> Tenant:
    user = User(id=uuid.uuid4(), external_auth_id="user_avail", email="owner@example.com")
    business = Business(
        id=uuid.uuid4(),
        owner_id=user.id,
        name="Alpha Salon",
        industry="Salon",
        address="1 Main St",
        timezone="America/New_York",
        phone="+1 (312) 847-1928",
        contact_email=None,
        currency="USD",
    )
    return Tenant(user=user, business=business)


class Salon:
    def __init__(self) -> None:
        self.tenant = _tenant()
        assert self.tenant.business is not None
        self.business_id = self.tenant.business.id
        self.directory_store = MemoryDirectoryStore()
        self.directory_store.set_week(self.business_id, WEEK)
        add = self.directory_store.add_service
        self.haircut = add(self.business_id, "Haircut", duration_minutes=45, price="40.00")
        self.kids = add(self.business_id, "Kids cut", duration_minutes=20, price="18.00")
        self.calendar = FakeBusySource()
        # Friday morning is taken: the afternoon offer is 12:00, 14:00, 16:15.
        self.calendar.add(self.business_id, FRIDAY, "09:00", "12:00")
        self.directory = BusinessDirectory(self.directory_store, lambda: NOW, RULES)

    def engine(self, store: MemoryStore, llm: FakeLLMClient) -> ConversationEngine:
        availability = AvailabilityService(self.directory, self.calendar, savepoint=store.savepoint)
        tools = ToolRegistry.of(
            [
                get_services_tool(self.directory),
                get_business_info_tool(self.directory),
                check_availability_tool(self.directory, availability, savepoint=store.savepoint),
            ]
        )
        return ConversationEngine(
            store,
            llm,
            tools,
            20,
            5,
            usage=guard(),
            llm_retry=RetryPolicy(attempts=2),
            directory=self.directory,
        )

    async def turn(self, store, llm, text: str, interaction_id=None):
        return await self.engine(store, llm).handle_turn(
            self.tenant, interaction_id, text, "test_chat"
        )


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def salon() -> Salon:
    return Salon()


def _check(service: str = "haircut", day: str = "2026-10-09", **extra) -> dict:
    arguments = {"service": service, "appointment_date": day, **extra}
    return agent_turn(
        "Checking.", tool_calls=[{"name": CHECK_AVAILABILITY, "arguments": arguments}]
    )


def _offer(text: str, salon: Salon, intent: str = "BOOK_APPOINTMENT") -> dict:
    return agent_turn(text, intent=intent, sources=[str(salon.haircut.id)])


def _state(store: MemoryStore) -> dict:
    return next(iter(store.interactions.values())).state


GOOD_OFFER = "I have 12:00, 2:00 or 4:15 PM on Friday. Which works?"


async def test_demo_offers_real_free_times_and_saves_them(store, salon):
    llm = FakeLLMClient()
    llm.queue(_check(time_of_day="afternoon"), _offer(GOOD_OFFER, salon))

    result = await salon.turn(store, llm, "Can I get a haircut Friday afternoon?")

    assert (result.fallback, result.reply_text) == (False, GOOD_OFFER)
    tool_results = llm.requests[1].system_instruction
    assert '"offered_times": ["12:00", "14:00", "16:15"]' in tool_results
    assert '"result": "available"' in tool_results
    # The calendar was asked about Friday's opening hours only.
    [(business_id, window)] = salon.calendar.calls
    assert business_id == salon.business_id and window.start.date() == FRIDAY
    offered = _state(store)[OFFERED_SLOTS]
    assert offered["service_id"] == str(salon.haircut.id) and offered["date"] == "2026-10-09"
    assert [slot["start"] for slot in offered["slots"]] == [
        "2026-10-09T12:00:00-04:00",
        "2026-10-09T14:00:00-04:00",
        "2026-10-09T16:15:00-04:00",
    ]
    [operation] = store.operations
    assert operation["tool_name"] == CHECK_AVAILABILITY and operation["status"] == "succeeded"
    assert operation["result"] == {
        "service_ids": [str(salon.haircut.id)],
        "date": "2026-10-09",
        "result": "available",
        "offered": 3,
        "free": 18,  # 12:00 .. 16:15 every 15 minutes
    }


async def test_the_prompt_gives_the_model_today_and_the_local_time(store, salon):
    llm = FakeLLMClient()
    llm.queue(agent_turn("Hi!"))
    await salon.turn(store, llm, "hi")
    assert "Today: Wednesday 2026-10-07, local time now 14:00" in llm.requests[0].system_instruction
    assert CHECK_AVAILABILITY in llm.requests[0].system_instruction


async def test_a_time_that_was_not_offered_is_regenerated_once(store, salon):
    llm = FakeLLMClient()
    llm.queue(
        _check(time_of_day="afternoon"),
        _offer("I have 1:00 PM or 2:00 PM on Friday.", salon),  # 1 PM was never offered
        _offer(GOOD_OFFER, salon),
    )

    result = await salon.turn(store, llm, "Can I get a haircut Friday afternoon?")

    assert (result.fallback, result.reply_text) == (False, GOOD_OFFER)
    assert len(llm.requests) == 3
    assert TIME_CORRECTION in llm.requests[2].system_instruction
    assert TIME_CORRECTION not in llm.requests[1].system_instruction
    assert [f["kind"] for f in _state(store)["failures"]] == ["unverified_time"]
    assert len(_state(store)[OFFERED_SLOTS]["slots"]) == 3


async def test_a_second_unverified_time_gets_the_fallback_and_clears_the_offer(store, salon):
    llm = FakeLLMClient()
    llm.queue(_check(), _offer(GOOD_OFFER, salon))
    first = await salon.turn(store, llm, "Haircut on Friday?")
    assert _state(store)[OFFERED_SLOTS] is not None

    llm.queue(
        _check(),
        _offer("How about 9:30 AM?", salon),
        _offer("9:45 AM is free too.", salon),
    )
    result = await salon.turn(store, llm, "anything earlier?", first.interaction_id)

    business = salon.tenant.business
    assert business is not None
    assert result.fallback is True
    assert result.reply_text == availability_fallback(business)
    assert "(312) 847-1928" in result.reply_text
    state = _state(store)
    assert state["requires_human"] is True
    assert state[OFFERED_SLOTS] is None  # the customer never heard this turn's offer


async def test_naming_a_time_without_checking_is_caught(store, salon):
    llm = FakeLLMClient()
    llm.queue(
        agent_turn("Sure, 3pm on Friday works!", intent="BOOK_APPOINTMENT"),
        agent_turn("Sure, see you at 3 on Friday!", intent="BOOK_APPOINTMENT"),
    )

    result = await salon.turn(store, llm, "Haircut at 3 on Friday?")

    assert result.fallback is True and salon.calendar.calls == []
    assert [f["kind"] for f in _state(store)["failures"]] == ["unverified_time"] * 2


async def test_opening_hours_are_not_availability_in_a_booking_reply(store, salon):
    llm = FakeLLMClient()
    llm.queue(
        agent_turn("We open at 9:00 AM on Friday, come by then.", intent="BOOK_APPOINTMENT"),
        agent_turn("We open at 9:00 AM on Friday.", intent="BOOK_APPOINTMENT"),
    )
    result = await salon.turn(store, llm, "Can I book Friday?")
    assert result.fallback is True
    assert [f["kind"] for f in _state(store)["failures"]] == ["unverified_time"] * 2


async def test_an_hours_reply_may_state_the_opening_and_closing_times(store, salon):
    llm = FakeLLMClient()
    llm.queue(
        agent_turn(
            "We're open 9:00 AM to 5:00 PM on Friday.",
            intent="BUSINESS_HOURS",
            sources=["business_hours"],
        )
    )
    result = await salon.turn(store, llm, "When are you open Friday?")
    assert result.fallback is False


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (CalendarUnavailableError("server_error"), "unavailable"),
        (CalendarNotSelectedError("not_connected"), "not_connected"),
    ],
    ids=["outage", "not-connected"],
)
async def test_calendar_down_is_answered_honestly_with_no_times(store, salon, error, reason):
    salon.calendar.error = error
    honest = "Sorry, I can't check availability right now. Please call +1 (312) 847-1928."
    llm = FakeLLMClient()
    llm.queue(_check(), agent_turn(honest, intent="BOOK_APPOINTMENT", sources=["business_profile"]))

    result = await salon.turn(store, llm, "Haircut on Friday?")

    assert (result.fallback, result.reply_text) == (False, honest)
    assert '"result": "calendar_unavailable", "service"' in llm.requests[1].system_instruction
    assert '"offered_times"' not in llm.requests[1].system_instruction
    state = _state(store)
    assert state["requires_human"] is True and state[OFFERED_SLOTS] is None
    [operation] = store.operations
    assert operation["result"]["result"] == "calendar_unavailable"
    assert operation["result"]["reason"] == reason


async def test_a_calendar_down_reply_that_invents_a_time_is_caught(store, salon):
    salon.calendar.error = CalendarUnavailableError("timeout")
    llm = FakeLLMClient()
    llm.queue(_check(), _offer("I have 2:00 PM.", salon), _offer("Try 4 PM.", salon))
    result = await salon.turn(store, llm, "Haircut on Friday?")
    assert result.fallback is True
    assert [f["kind"] for f in _state(store)["failures"]] == ["unverified_time"] * 2


async def test_closed_day_is_a_verified_no_availability(store, salon):
    llm = FakeLLMClient()
    llm.queue(
        _check(day="2026-10-11"),
        agent_turn("We're closed on Sunday. Another day?", intent="BOOK_APPOINTMENT"),
    )
    result = await salon.turn(store, llm, "Haircut on Sunday?")
    assert result.fallback is False
    assert '"reason": "closed"' in llm.requests[1].system_instruction
    assert salon.calendar.calls == []


async def test_an_ambiguous_service_is_asked_about_before_any_calendar_call(store, salon):
    llm = FakeLLMClient()
    llm.queue(
        _check(service="cut"), agent_turn("A haircut or a kids cut?", intent="BOOK_APPOINTMENT")
    )
    result = await salon.turn(store, llm, "Can I get a cut Friday?")
    assert result.fallback is False
    assert '"result": "ambiguous_service"' in llm.requests[1].system_instruction
    assert salon.calendar.calls == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"service": "haircut", "appointment_date": "Friday"},
        {"service": "haircut"},
        {"service": "haircut", "appointment_date": "2026-10-09", "around": "3pm"},
        {"service": "haircut", "appointment_date": "2026-10-09", "time_of_day": "night"},
        {"service": "haircut", "appointment_date": "2026-10-09", "business_id": str(uuid.uuid4())},
    ],
    ids=["date-words", "no-date", "around-12h", "bad-part-of-day", "smuggled-business"],
)
async def test_invalid_arguments_are_rejected_and_never_reach_the_calendar(store, salon, arguments):
    call = agent_turn(
        "Checking.", tool_calls=[{"name": CHECK_AVAILABILITY, "arguments": arguments}]
    )
    llm = FakeLLMClient()
    llm.queue(call, agent_turn("Which day would you like?", intent="BOOK_APPOINTMENT"))
    await salon.turn(store, llm, "Haircut?")
    assert salon.calendar.calls == []
    [operation] = store.operations
    assert operation["status"] == "rejected"


async def test_tool_runs_outside_the_savepoint_but_reads_inside_one(store, salon):
    llm = FakeLLMClient()
    llm.queue(_check(), _offer(GOOD_OFFER, salon))
    await salon.turn(store, llm, "Haircut Friday?")
    # The service lookup and the directory reads each open their own savepoint; the
    # executor itself doesn't wrap the tool in one (it must be free to commit).
    tool_calls = store.calls[store.calls.index("checkpoint") :]
    assert tool_calls.count("savepoint") == 2


# --- review follow-ups: intent can't unlock hours, last offer wins, earlier offers --------


async def test_an_hours_intent_cannot_offer_an_opening_time_as_free(store, salon):
    sneaky = agent_turn(
        "Sure, 9:00 AM on Friday works!", intent="BUSINESS_HOURS", sources=["business_hours"]
    )
    llm = FakeLLMClient()
    llm.queue(sneaky, sneaky)
    result = await salon.turn(store, llm, "Can I come Friday at 9?")
    assert result.fallback is True
    assert [f["kind"] for f in _state(store)["failures"]] == ["unverified_time"] * 2


async def test_the_last_offer_in_a_turn_wins(store, salon):
    # Thursday is busy from 11:00: its offer is 09:00, 09:30, 10:15.
    salon.calendar.add(salon.business_id, date(2026, 10, 8), "11:00", "17:00")
    two_checks = agent_turn(
        "Checking.",
        tool_calls=[
            {
                "name": CHECK_AVAILABILITY,
                "arguments": {"service": "haircut", "appointment_date": "2026-10-09"},
            },
            {
                "name": CHECK_AVAILABILITY,
                "arguments": {"service": "haircut", "appointment_date": "2026-10-08"},
            },
        ],
    )
    llm = FakeLLMClient()
    llm.queue(
        two_checks,
        _offer("Friday at 2:00 PM works.", salon),  # Friday's offer was replaced
        _offer("On Thursday I have 9:00, 9:30 or 10:15 AM.", salon),
    )

    result = await salon.turn(store, llm, "Haircut Thursday or Friday?")

    assert result.fallback is False and result.reply_text.startswith("On Thursday")
    offered = _state(store)[OFFERED_SLOTS]
    assert offered["date"] == "2026-10-08" and len(offered["slots"]) == 3


async def test_a_later_no_availability_keeps_this_turns_offer(store, salon):
    llm = FakeLLMClient()
    llm.queue(
        agent_turn(
            "Checking.",
            tool_calls=[
                {
                    "name": CHECK_AVAILABILITY,
                    "arguments": {"service": "haircut", "appointment_date": "2026-10-09"},
                },
                {
                    "name": CHECK_AVAILABILITY,
                    "arguments": {"service": "haircut", "appointment_date": "2026-10-11"},
                },
            ],
        ),
        _offer("Sunday we're closed, but Friday I have 12:00, 2:00 or 4:15 PM.", salon),
    )
    result = await salon.turn(store, llm, "Haircut Friday or Sunday?")
    assert result.fallback is False
    assert _state(store)[OFFERED_SLOTS]["date"] == "2026-10-09"


async def test_the_next_turn_may_repeat_a_time_from_the_offer_the_customer_heard(store, salon):
    llm = FakeLLMClient()
    llm.queue(_check(time_of_day="afternoon"), _offer(GOOD_OFFER, salon))
    first = await salon.turn(store, llm, "Can I get a haircut Friday afternoon?")

    llm.queue(agent_turn("Great, 2:00 PM on Friday. Shall I book it?", intent="BOOK_APPOINTMENT"))
    result = await salon.turn(store, llm, "2 works", first.interaction_id)

    assert result.fallback is False
    prompt = llm.requests[-1].system_instruction
    assert (
        "TIMES OFFERED IN YOUR LAST REPLY (local): Friday 2026-10-09: 12:00, 14:00, 16:15" in prompt
    )
    assert salon.calendar.calls[1:] == []  # no new lookup was needed to repeat it


async def test_the_next_turn_cannot_name_a_time_that_was_never_offered(store, salon):
    llm = FakeLLMClient()
    llm.queue(_check(time_of_day="afternoon"), _offer(GOOD_OFFER, salon))
    first = await salon.turn(store, llm, "Can I get a haircut Friday afternoon?")

    nope = agent_turn("Sure, 3:00 PM on Friday works too.", intent="BOOK_APPOINTMENT")
    llm.queue(nope, nope)
    result = await salon.turn(store, llm, "what about 3?", first.interaction_id)
    assert result.fallback is True


async def test_tomorrow_is_resolved_by_the_backend_in_the_business_timezone(store, salon):
    llm = FakeLLMClient()
    llm.queue(
        _check(day="tomorrow"),
        agent_turn("Let me know which day works.", intent="BOOK_APPOINTMENT"),
    )
    await salon.turn(store, llm, "Haircut tomorrow?")
    assert '"date": "2026-10-08"' in llm.requests[1].system_instruction
    [(_, window)] = salon.calendar.calls
    assert window.start.date() == date(2026, 10, 8)


async def test_a_date_in_words_is_rejected_with_the_format_and_today(store, salon):
    llm = FakeLLMClient()
    llm.queue(
        _check(day="next Friday"),
        agent_turn("Which date would you like?", intent="BOOK_APPOINTMENT"),
    )
    await salon.turn(store, llm, "Haircut next Friday?")
    feedback = llm.requests[1].system_instruction
    assert '"error": "invalid_date"' in feedback
    assert '"expected": "YYYY-MM-DD"' in feedback and '"today": "2026-10-07"' in feedback
    assert salon.calendar.calls == []
    [operation] = store.operations
    assert operation["status"] == "rejected"


async def test_a_fresh_fully_booked_check_retires_the_earlier_offer(store, salon):
    llm = FakeLLMClient()
    llm.queue(_check(time_of_day="afternoon"), _offer(GOOD_OFFER, salon))
    first = await salon.turn(store, llm, "Can I get a haircut Friday afternoon?")

    # Someone took the rest of Friday since: a re-check finds nothing.
    salon.calendar.add(salon.business_id, FRIDAY, "12:00", "17:00")
    stale = agent_turn("2:00 PM on Friday is still free!", intent="BOOK_APPOINTMENT")
    llm.queue(_check(), stale, stale)
    result = await salon.turn(store, llm, "is 2 still free?", first.interaction_id)

    assert '"reason": "fully_booked"' in llm.requests[-1].system_instruction
    assert result.fallback is True
    state = _state(store)
    assert state[OFFERED_SLOTS] is None
    assert [f["kind"] for f in state["failures"]] == ["unverified_time"] * 2
