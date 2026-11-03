"""Provider failures through the real API and engine: nothing false is ever confirmed,
retries stay bounded, the conversation survives, and the failure is logged as a code.

Every test breaks a provider fake with `ProviderChaos` (the exceptions the REAL adapters
raise) and drives HTTP endpoints; assertions are on replies, stored rows and the number
of provider attempts. Expected attempt counts come from the retry policies' documented
budgets (2 per call), not from re-running the code's own arithmetic.
"""

import pytest

from integrations.llm.fake import agent_turn
from services.availability_tool import CHECK_AVAILABILITY, OFFERED_SLOTS
from services.calendar import CALENDAR_RETRY
from services.conversation import PROVIDER_FALLBACK
from services.grounding import booking_claims, clock_times
from services.usage import UsageKind
from tests.api.test_bookings import BOOKED, FRIDAY, _book_call, _connect, _salon
from tests.chaos.chaos import CANARY, FAULTS, Fault
from tests.chaos.support import (
    CUSTOMER_CANARY,
    CUSTOMER_PHONE,
    CUSTOMER_TEXT,
    OWNER_PHONE,
    assert_logs_are_clean,
    auth,
    booking_statuses,
    chat,
    customer_message_count,
    interaction_state,
    operations,
    stored_messages,
    tether_records,
)

PRIVATE = (CUSTOMER_CANARY, CUSTOMER_PHONE, "5557654321", OWNER_PHONE)
FAULT_IDS = list(FAULTS)
# The documented budgets: one model call is at most 2 attempts, one Google call at most 2.
LLM_ATTEMPTS = 2
GOOGLE_ATTEMPTS = CALENDAR_RETRY.attempts


# --- Gemini --------------------------------------------------------------------------


@pytest.mark.parametrize("fault", FAULT_IDS)
async def test_a_gemini_failure_gets_the_provider_fallback_and_keeps_the_conversation(
    client, entitled_tenants, chaos, llm, tether_logs, fault: Fault
):
    tenant, _ = entitled_tenants
    chaos.fail("gemini", fault, retry_after_seconds=39_801.0)

    turn = await chat(client, tenant)

    assert turn["reply_text"] == PROVIDER_FALLBACK and turn["fallback"] is True
    assert chaos.calls("gemini") == LLM_ATTEMPTS  # one retry, never more
    assert llm.requests == []  # nothing reached the model
    assert max(chaos.sleeps, default=0) <= 1.0  # a 429's long retry-after is never waited out
    # The interaction and the customer's message are stored, with the fallback after it.
    messages = await stored_messages(client, tenant, turn["interaction_id"])
    assert CUSTOMER_TEXT in [m["text"] for m in messages if m["speaker"] == "customer"]
    assert messages[-1]["speaker"] == "agent" and messages[-1]["fallback"] is True
    state = await interaction_state(turn["interaction_id"])
    assert [(f["kind"], f["detail"]) for f in state["failures"]] == [
        ("provider_unavailable", "llm_unavailable")
    ]
    # Every attempt is counted as a failed request.
    usage_body = (await client.get("/api/v1/usage", headers=auth(tenant))).json()
    assert usage_body["failed_requests"] == LLM_ATTEMPTS
    assert CANARY not in str(usage_body)
    # Logged as a fixed reason code on the engine's logger.
    [failed] = [
        r
        for r in tether_records(tether_logs, "tether.conversation")
        if r.getMessage() == "model turn failed"
    ]
    assert failed.__dict__["failure_kind"] == "provider_unavailable"
    assert_logs_are_clean(tether_logs, *PRIVATE)


async def test_a_gemini_outage_across_turns_is_bounded_and_recovers(
    client, entitled_tenants, chaos, llm
):
    tenant, _ = entitled_tenants
    chaos.fail("gemini", "server_error")  # every call until healed

    first = await chat(client, tenant)
    interaction_id = first["interaction_id"]
    replies = [first["reply_text"]]
    for index in range(3):
        again = await chat(client, tenant, f"Still there? {index}", interaction_id)
        replies.append(again["reply_text"])

    assert replies == [PROVIDER_FALLBACK] * 4
    assert chaos.calls("gemini") == 4 * LLM_ATTEMPTS  # exactly attempts-per-call x calls
    assert await customer_message_count(interaction_id) == 4  # no turn was lost

    chaos.heal("gemini")
    llm.queue(agent_turn("Sorry about that, how can I help?"))
    recovered = await chat(client, tenant, "Hello again", interaction_id)
    assert recovered["fallback"] is False
    assert chaos.calls("gemini") == 4 * LLM_ATTEMPTS + 1


async def test_a_reached_usage_limit_during_an_outage_makes_no_more_provider_calls(
    client, entitled_tenants, chaos, usage
):
    tenant, _ = entitled_tenants
    usage.set_daily(UsageKind.GEMINI_REQUEST, per_day=1_000_000, per_business_per_day=3)
    chaos.fail("gemini", "timeout")

    first = await chat(client, tenant)  # attempts 1 and 2
    second = await chat(
        client, tenant, "Hello?", first["interaction_id"]
    )  # attempt 3, then the cap
    third = await chat(client, tenant, "Anyone?", first["interaction_id"])

    assert first["fallback"] is True and first["usage_limited"] is False
    assert second["usage_limited"] is True and third["usage_limited"] is True
    assert chaos.calls("gemini") == 3  # the cap, not the retry policy, stops it


async def test_a_zero_limit_makes_zero_gemini_calls_even_when_the_provider_is_down(
    client, entitled_tenants, chaos, usage
):
    tenant, _ = entitled_tenants
    usage.set_daily(UsageKind.GEMINI_REQUEST, per_day=1_000_000, per_business_per_day=0)
    chaos.fail("gemini", "rate_limited")

    turn = await chat(client, tenant)

    assert turn["usage_limited"] is True
    assert chaos.calls("gemini") == 0


# --- Google Calendar: bookings -----------------------------------------------------------


async def _salon_with_calendar(client, tenant, calendar) -> tuple[str, str]:
    service_id = await _salon(client, tenant)
    refresh = await _connect(client, tenant, calendar)
    return service_id, refresh


@pytest.mark.parametrize("fault", FAULT_IDS)
async def test_a_calendar_failure_while_booking_confirms_nothing(
    client, entitled_tenants, calendar, chaos, llm, tether_logs, fault: Fault
):
    tenant, _ = entitled_tenants
    service_id, refresh = await _salon_with_calendar(client, tenant, calendar)
    chaos.fail("calendar", fault, op="insert_event")
    # A model that lies: it claims the booking both times it is asked again.
    llm.queue(
        _book_call(),
        agent_turn("You're all set!", intent="BOOK_APPOINTMENT", sources=[service_id]),
        agent_turn("You're booked!", intent="BOOK_APPOINTMENT", sources=[service_id]),
    )

    turn = await chat(client, tenant, f"3:30 please, I'm Alex {CUSTOMER_CANARY}")

    assert turn["fallback"] is True
    assert booking_claims(turn["reply_text"]) == []  # the customer is never told it's booked
    assert await booking_statuses(tenant.business_id) == ["failed"]  # no `confirmed` row
    assert calendar.api.events(refresh) == {}  # and nothing is left on the owner's calendar
    listed = await client.get(
        "/api/v1/bookings", params={"interaction_id": turn["interaction_id"]}, headers=auth(tenant)
    )
    assert [b["status"] for b in listed.json()["bookings"]] == ["failed"]
    # Bounded: the insert was attempted per CALENDAR_RETRY, every other Google op too.
    assert chaos.calls("calendar", "insert_event") == GOOGLE_ATTEMPTS
    for op in ("free_busy", "delete_event", "refresh_access_token"):
        assert chaos.calls("calendar", op) <= 2 * GOOGLE_ATTEMPTS
    # The operation log has the failure as a code, the interaction is flagged for a human.
    [operation] = await operations(turn["interaction_id"], "create_booking")
    assert operation.result["result"] == "booking_failed"
    state = await interaction_state(turn["interaction_id"])
    assert state["requires_human"] is True
    customer_texts = [
        m["text"]
        for m in await stored_messages(client, tenant, turn["interaction_id"])
        if m["speaker"] == "customer"
    ]
    assert customer_texts == [f"3:30 please, I'm Alex {CUSTOMER_CANARY}"]
    assert_logs_are_clean(tether_logs, *PRIVATE)


async def test_an_event_created_but_the_answer_lost_is_compensated(
    client, entitled_tenants, calendar, chaos, llm
):
    tenant, _ = entitled_tenants
    service_id, refresh = await _salon_with_calendar(client, tenant, calendar)
    # Google creates the event, then every reply times out.
    chaos.fail("calendar", "timeout", op="insert_event", lands=True)
    llm.queue(
        _book_call(),
        agent_turn("You're all set!", intent="BOOK_APPOINTMENT", sources=[service_id]),
        agent_turn("You're booked!", intent="BOOK_APPOINTMENT", sources=[service_id]),
    )

    turn = await chat(client, tenant, "3:30 please, I'm Alex")

    assert booking_claims(turn["reply_text"]) == []
    assert "confirmed" not in await booking_statuses(tenant.business_id)
    assert calendar.api.events(refresh) == {}  # the orphan event was deleted again
    assert chaos.calls("calendar", "insert_event") == GOOGLE_ATTEMPTS


async def test_one_transient_calendar_failure_is_retried_into_exactly_one_booking(
    client, entitled_tenants, calendar, chaos, llm
):
    tenant, _ = entitled_tenants
    service_id, refresh = await _salon_with_calendar(client, tenant, calendar)
    chaos.fail("calendar", "rate_limited", op="insert_event", times=1)
    llm.queue(_book_call(), agent_turn(BOOKED, intent="BOOK_APPOINTMENT", sources=[service_id]))

    turn = await chat(client, tenant, "3:30 please, I'm Alex")

    assert turn["reply_text"] == BOOKED and turn["fallback"] is False
    assert chaos.calls("calendar", "insert_event") == 2  # one failure, one retry
    assert await booking_statuses(tenant.business_id) == ["confirmed"]
    assert len(calendar.api.events(refresh)) == 1  # the retry did not double-book


@pytest.mark.parametrize("fault", FAULT_IDS)
async def test_a_calendar_failure_while_checking_availability_offers_no_times(
    client, entitled_tenants, calendar, chaos, llm, fault: Fault
):
    tenant, _ = entitled_tenants
    service_id, _ = await _salon_with_calendar(client, tenant, calendar)
    chaos.fail("calendar", fault, op="free_busy")
    check = agent_turn(
        "Checking.",
        tool_calls=[
            {
                "name": CHECK_AVAILABILITY,
                "arguments": {"service": "haircut", "appointment_date": FRIDAY.isoformat()},
            }
        ],
    )
    invented = agent_turn(
        "I have 3:00 PM or 3:30 PM on Friday.", intent="BOOK_APPOINTMENT", sources=[service_id]
    )
    llm.queue(check, invented, invented)

    turn = await chat(client, tenant, "Anything free Friday afternoon?")

    assert turn["fallback"] is True
    assert clock_times(turn["reply_text"]) == []  # no time reaches the customer
    state = await interaction_state(turn["interaction_id"])
    assert state[OFFERED_SLOTS] is None and state["requires_human"] is True
    assert chaos.calls("calendar", "free_busy") == GOOGLE_ATTEMPTS
    assert await booking_statuses(tenant.business_id) == []


# --- Twilio SMS: the escalation alert ----------------------------------------------------

SETTINGS = {
    "contact_phone": OWNER_PHONE,
    "sms_enabled": True,
    "clarification_threshold": 3,
    "urgent_keywords": [],
}
# (fault, SMS attempts, stored sms_error): Twilio's Messages API has no idempotency key, so
# a timeout or 5xx is `send_unconfirmed` and sent ONCE; only a 429 (nothing created) retries.
SMS_CASES = [
    pytest.param("rate_limited", 2, "rate_limited", id="429-retried-once"),
    pytest.param("timeout", 1, "send_unconfirmed", id="timeout-never-resent"),
    pytest.param("server_error", 1, "send_unconfirmed", id="5xx-never-resent"),
]


@pytest.mark.parametrize(("fault", "attempts", "sms_error"), SMS_CASES)
async def test_an_sms_failure_during_an_escalation_is_bounded_recorded_and_not_fatal(
    client, entitled_tenants, chaos, llm, sms, tether_logs, fault: Fault, attempts, sms_error
):
    tenant, _ = entitled_tenants
    saved = await client.put(
        "/api/v1/business/escalation-settings", json=SETTINGS, headers=auth(tenant)
    )
    assert saved.status_code == 200, saved.text
    chaos.fail("twilio_sms", fault)
    llm.queue(
        agent_turn("I will pass that on.", intent="HUMAN_REQUEST", requires_human=True),
        agent_turn("Your request has been noted.", intent="HUMAN_REQUEST"),
    )

    first = await chat(client, tenant, "I want to talk to a person")
    again = await chat(client, tenant, "hello? a person please", first["interaction_id"])

    assert first["escalated"] is True and first["fallback"] is False  # the turn still succeeds
    assert again["fallback"] is False
    assert chaos.calls("twilio_sms") == attempts  # not re-sent by the second turn either
    assert sms.sent == []
    listed = await client.get("/api/v1/escalations", params={"status": "all"}, headers=auth(tenant))
    [escalation] = listed.json()["items"]  # recorded once
    assert escalation["reason"] == "human_requested"
    assert (escalation["sms_status"], escalation["sms_error"]) == ("failed", sms_error)
    [alert] = [
        r
        for r in tether_records(tether_logs, "tether.escalation")
        if r.getMessage() == "escalation alert not sent"
    ]
    assert alert.__dict__["reason"] == "human_requested"
    assert_logs_are_clean(tether_logs, OWNER_PHONE, "5551234567")


async def test_a_reached_sms_limit_makes_no_provider_call_but_still_records_the_escalation(
    client, entitled_tenants, chaos, llm, usage
):
    tenant, _ = entitled_tenants
    await client.put("/api/v1/business/escalation-settings", json=SETTINGS, headers=auth(tenant))
    usage.set_daily(UsageKind.SMS, per_day=1_000_000, per_business_per_day=0)
    chaos.fail("twilio_sms", "rate_limited")
    llm.queue(agent_turn("I will pass that on.", intent="HUMAN_REQUEST", requires_human=True))

    turn = await chat(client, tenant, "I want to talk to a person")

    assert turn["escalated"] is True
    assert chaos.calls("twilio_sms") == 0
    listed = await client.get("/api/v1/escalations", params={"status": "all"}, headers=auth(tenant))
    [escalation] = listed.json()["items"]
    assert escalation["sms_status"] == "failed"
