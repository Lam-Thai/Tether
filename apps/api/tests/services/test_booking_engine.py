"""`create_booking` through the conversation engine: scripted fake model, in-memory stores,
the real `BookingService` over a fake calendar. No DB, no Google, no Gemini.

Covers the slice's safety property end to end: the reply says "booked" only when the
backend confirmed a booking this turn (the booking guard); a calendar failure gets an
honest reply and a human flag; a taken slot becomes a fresh, verified offer.
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
from services.booking import BookingService
from services.booking_rules import BookingRules
from services.booking_tool import CREATE_BOOKING, create_booking_tool
from services.business_tools import get_services_tool
from services.calendar import CalendarUnavailableError
from services.conversation import BOOKING_CORRECTION, ConversationEngine, booking_fallback
from services.directory import BusinessDirectory
from services.tools import ToolRegistry
from tests.services.fake_booking_calendar import FakeBookingCalendar
from tests.services.memory_booking_store import MemoryBookingStore
from tests.services.memory_conversation_store import MemoryStore
from tests.services.memory_directory_store import MemoryDirectoryStore
from tests.services.memory_usage_store import guard

# Wednesday 2026-10-07, 14:00 in New York.
NOW = datetime(2026, 10, 7, 18, 0, tzinfo=UTC)
FRIDAY = date(2026, 10, 9)
WEEK = {d: ("09:00", "17:00") for d in range(5)} | {5: None, 6: None}
RULES = BookingRules(15, 120, 60, 0)
BOOKED = "You're booked for a haircut on Friday at 3:30 PM, Alex."


def _tenant() -> Tenant:
    user = User(id=uuid.uuid4(), external_auth_id="user_book_engine", email="o@example.com")
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
        self.business = self.tenant.business
        self.business_id = self.business.id
        directory_store = MemoryDirectoryStore()
        directory_store.set_week(self.business_id, WEEK)
        add = directory_store.add_service
        self.haircut = add(self.business_id, "Haircut", duration_minutes=45, price="40.00")
        self.kids = add(self.business_id, "Kids cut", duration_minutes=20, price="18.00")
        self.calendar = FakeBookingCalendar()
        self.calendar.add_busy(self.business_id, FRIDAY, "09:00", "12:00")
        self.bookings = MemoryBookingStore()
        self.directory = BusinessDirectory(directory_store, lambda: NOW, RULES)

    def engine(self, store: MemoryStore, llm: FakeLLMClient) -> ConversationEngine:
        availability = AvailabilityService(self.directory, self.calendar, savepoint=store.savepoint)
        booking = BookingService(
            self.directory,
            AvailabilityService(self.directory, self.calendar, savepoint=self.bookings.savepoint),
            self.calendar,
            self.bookings,
        )
        tools = ToolRegistry.of(
            [
                get_services_tool(self.directory),
                check_availability_tool(self.directory, availability, savepoint=store.savepoint),
                create_booking_tool(self.directory, booking, savepoint=store.savepoint),
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


def _book(service: str = "haircut", start: str = "15:30", **extra) -> dict:
    arguments = {
        "service": service,
        "appointment_date": "2026-10-09",
        "start_time": start,
        "customer_name": "Alex",
        **extra,
    }
    return agent_turn("Booking.", tool_calls=[{"name": CREATE_BOOKING, "arguments": arguments}])


def _reply(text: str, salon: Salon) -> dict:
    return agent_turn(text, intent="BOOK_APPOINTMENT", sources=[str(salon.haircut.id)])


def _state(store: MemoryStore) -> dict:
    return next(iter(store.interactions.values())).state


async def test_demo_books_then_confirms_from_the_verified_result(store, salon):
    llm = FakeLLMClient()
    llm.queue(_book(), _reply(BOOKED, salon))

    result = await salon.turn(store, llm, "3:30 please, I'm Alex")

    assert (result.fallback, result.reply_text) == (False, BOOKED)
    tool_results = llm.requests[1].system_instruction
    assert '"result": "confirmed"' in tool_results
    assert '"start_time": "15:30"' in tool_results and '"duration_minutes": 45' in tool_results
    [booking] = salon.bookings.of(salon.business_id)
    assert booking.status == "confirmed" and booking.calendar_event_id
    assert booking.interaction_id == result.interaction_id
    [operation] = store.operations
    assert (operation["tool_name"], operation["status"]) == (CREATE_BOOKING, "succeeded")
    assert operation["result"]["result"] == "confirmed"
    assert operation["result"]["booking_id"] == str(booking.id)
    assert "Alex" not in str(operation["result"])  # the log's summary holds no customer text
    assert _state(store)[OFFERED_SLOTS] is None


async def test_a_booking_that_was_made_is_never_denied_by_a_fallback(store, salon):
    llm = FakeLLMClient()
    # The booking is confirmed, but both replies name a time that wasn't booked.
    llm.queue(
        _book(),
        _reply("You're booked for 4:15 PM.", salon),
        _reply("You're booked for 4:15 PM, Alex.", salon),
    )

    result = await salon.turn(store, llm, "3:30 please, I'm Alex")

    assert result.fallback is True
    assert result.reply_text == (
        "You're booked: Haircut on Friday, October 9 at 3:30 PM, under the name Alex."
    )
    assert [b.status for b in salon.bookings.of(salon.business_id)] == ["confirmed"]
    assert _state(store)["requires_human"] is True  # the model failed; a person checks


async def test_a_retried_tool_call_books_once(store, salon):
    llm = FakeLLMClient()
    arguments = _book()["tool_calls"][0]
    llm.queue(agent_turn("Booking.", tool_calls=[arguments, arguments]), _reply(BOOKED, salon))

    await salon.turn(store, llm, "3:30 please, I'm Alex")

    assert salon.calendar.created == 1
    assert len(salon.bookings.of(salon.business_id)) == 1
    replayed = [op["result"]["replayed"] for op in store.operations]
    assert replayed == [False, True]


async def test_claiming_a_booking_without_one_is_regenerated_once(store, salon):
    llm = FakeLLMClient()
    honest = "Happy to book that. What name should I put it under?"
    llm.queue(
        agent_turn("You're all set for Friday!", intent="BOOK_APPOINTMENT"),
        agent_turn(honest, intent="BOOK_APPOINTMENT"),
    )

    result = await salon.turn(store, llm, "Book me a haircut Friday at 3:30")

    assert (result.fallback, result.reply_text) == (False, honest)
    assert BOOKING_CORRECTION in llm.requests[1].system_instruction
    assert [f["kind"] for f in _state(store)["failures"]] == ["unconfirmed_booking"]
    assert salon.calendar.created == 0


async def test_a_second_false_claim_gets_the_booking_fallback(store, salon):
    llm = FakeLLMClient()
    llm.queue(
        agent_turn("You're booked!", intent="BOOK_APPOINTMENT"),
        agent_turn("I've booked you in.", intent="BOOK_APPOINTMENT"),
    )

    result = await salon.turn(store, llm, "Just book it")

    assert result.fallback is True
    assert result.reply_text == booking_fallback(salon.business)
    assert "(312) 847-1928" in result.reply_text
    assert _state(store)["requires_human"] is True


async def test_a_calendar_failure_is_never_reported_as_booked(store, salon):
    salon.calendar.create_error = CalendarUnavailableError("server_error")
    llm = FakeLLMClient()
    llm.queue(
        _book(),
        _reply("Great news, you're all set for 3:30 PM!", salon),
        _reply("I'm sorry, I couldn't complete that booking, so nothing is booked.", salon),
    )

    result = await salon.turn(store, llm, "3:30 please, I'm Alex")

    assert result.fallback is False and "couldn't complete" in result.reply_text
    assert '"result": "booking_failed"' in llm.requests[1].system_instruction
    assert BOOKING_CORRECTION in llm.requests[2].system_instruction
    assert [b.status for b in salon.bookings.of(salon.business_id)] == ["failed"]
    assert _state(store)["requires_human"] is True


async def test_a_taken_slot_becomes_a_fresh_offer(store, salon):
    salon.calendar.add_busy(salon.business_id, FRIDAY, "15:00", "16:00")
    llm = FakeLLMClient()
    llm.queue(
        _book(),
        _reply("Sorry, that time was just taken. I have 2:15, 4:00 or 4:15 PM.", salon),
    )

    result = await salon.turn(store, llm, "3:30 please, I'm Alex")

    assert result.fallback is False
    tool_results = llm.requests[1].system_instruction
    assert '"result": "slot_taken"' in tool_results
    # The free starts nearest 15:30 (15:00-16:00 is busy).
    assert '"offered_times": ["14:15", "16:00", "16:15"]' in tool_results
    offered = _state(store)[OFFERED_SLOTS]
    assert [slot["start"][11:16] for slot in offered["slots"]] == ["14:15", "16:00", "16:15"]
    assert salon.bookings.rows == []


async def test_the_booked_time_is_the_only_time_the_reply_may_state(store, salon):
    llm = FakeLLMClient()
    llm.queue(
        agent_turn(
            "Checking.",
            tool_calls=[
                {
                    "name": CHECK_AVAILABILITY,
                    "arguments": {"service": "haircut", "appointment_date": "2026-10-09"},
                }
            ],
        ),
        _book(),
        _reply("You're booked for 3:30 PM. I also have 4:15 PM if you'd prefer.", salon),
        _reply(BOOKED, salon),
    )

    result = await salon.turn(store, llm, "haircut friday 3:30, I'm Alex")

    assert (result.fallback, result.reply_text) == (False, BOOKED)
    assert [f["kind"] for f in _state(store)["failures"]] == ["unverified_time"]


async def test_a_missing_name_is_rejected_without_booking(store, salon):
    llm = FakeLLMClient()
    arguments = {"service": "haircut", "appointment_date": "2026-10-09", "start_time": "15:30"}
    llm.queue(
        agent_turn("Booking.", tool_calls=[{"name": CREATE_BOOKING, "arguments": arguments}]),
        agent_turn("What name should I put the booking under?", intent="BOOK_APPOINTMENT"),
    )

    result = await salon.turn(store, llm, "3:30 please")

    assert result.fallback is False
    [operation] = store.operations
    assert operation["status"] == "rejected"
    assert operation["result"] == {"reason": "invalid_arguments", "fields": ["customer_name"]}
    assert salon.bookings.rows == [] and salon.calendar.created == 0


async def test_an_ambiguous_service_asks_instead_of_booking(store, salon):
    llm = FakeLLMClient()
    llm.queue(_book(service="cut"), _reply("Did you mean a Haircut or a Kids cut?", salon))

    result = await salon.turn(store, llm, "a cut at 3:30, I'm Alex")

    assert result.fallback is False
    assert '"result": "ambiguous_service"' in llm.requests[1].system_instruction
    assert salon.bookings.rows == []


async def test_a_cancel_claim_is_never_allowed(store, salon):
    llm = FakeLLMClient()
    honest = "I can't cancel bookings myself. Please call the team at +1 (312) 847-1928."
    llm.queue(
        agent_turn("Your appointment has been cancelled.", intent="CANCEL"),
        agent_turn(honest, intent="CANCEL", requires_human=True),
    )

    result = await salon.turn(store, llm, "Cancel my Friday haircut, I'm Alex")

    assert (result.fallback, result.reply_text) == (False, honest)
    assert _state(store)["requires_human"] is True
    assert [f["kind"] for f in _state(store)["failures"]] == ["unconfirmed_booking"]
