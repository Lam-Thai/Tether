"""The grounding guard at its boundary: amount detection and `TurnEvidence.problem`. Pure."""

import uuid
from datetime import time
from decimal import Decimal

import pytest

from integrations.llm.fake import agent_turn
from schemas.agent_turn import AgentTurn
from services.grounding import (
    BUSINESS_HOURS,
    BUSINESS_PROFILE,
    CALLBACK_CORRECTION,
    GROUNDED_INTENTS,
    PROMISED_CALLBACK,
    UNCONFIRMED_BOOKING,
    UNVERIFIED_TIME,
    Evidence,
    TurnEvidence,
    booking_claims,
    claims_team_notified,
    clock_times,
    duration_minutes,
    grounding_problem,
    money_amounts,
)
from services.knowledge import FaqSnippet


def _d(*values: str) -> list[Decimal]:
    return [Decimal(v) for v in values]


# --- Amount detection --------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "amounts"),
    [
        ("A beard trim is $15.", ["15"]),
        ("It's $40.00 plus tax.", ["40"]),
        ("CA$ 40, US$40 or A$40.", ["40", "40", "40"]),
        ("Prices run €40-€50.", ["40", "50"]),
        ("That's 40 €, or 40,50 € with a wash.", ["40", "40.50"]),
        ("USD 40 or 55 USD.", ["40", "55"]),
        ("forty dollars, or a hundred and twenty bucks", ["40", "120"]),
        ("Kids cut for 18 dollars.", ["18"]),
        ("It's 1,500.50 dollars.", ["1500.50"]),
        ("A colour is 150.000 ₫ or VND 150,000.", ["150000", "150000"]),
        ("Just 50 cents more.", ["0.50"]),
        ("Between 40 and 60 dollars.", ["60"]),  # "40 and" is not a range: 40 isn't money
    ],
)
def test_money_amounts_are_read_next_to_a_currency_marker(text, amounts):
    assert sorted(money_amounts(text)) == sorted(_d(*amounts))


@pytest.mark.parametrize(
    "text",
    [
        "We're open 9:00 to 17:00, and 10 AM to 4 PM on Saturday.",
        "Call +1 (312) 847-1928 or visit 12 Elm St, Suite 300.",
        "Open October 24, 2026; 7 days a week; kids under 12.",
        "Late cancellations may be charged 50% of the price.",
        "Try 3 options, all 5 stylists; see you at 5 p.m.",
        "Our 2 locations: 100 Example Street.",
    ],
)
def test_times_dates_phones_and_street_numbers_are_not_amounts(text):
    assert money_amounts(text) == []
    assert duration_minutes(text) == []


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("It takes 45 minutes.", ["45"]),
        ("A 90-minute session.", ["90"]),
        ("About 1.5 hours.", ["90"]),
        ("1 hour 30 minutes", ["90"]),
        ("2 hrs and 15 mins", ["135"]),
        ("an hour and a half", ["90"]),
        ("half an hour", ["30"]),
        ("a quarter of an hour", ["15"]),
        ("about an hour", ["60"]),
        ("two and a half hours", ["150"]),
        ("forty-five minutes", ["45"]),
        ("15 or 20 minutes", ["15", "20"]),
        ("30-45 min", ["30", "45"]),
        ("cancel 24 hours ahead", ["1440"]),
    ],
)
def test_durations_are_read_in_minutes(text, minutes):
    assert sorted(duration_minutes(text)) == sorted(_d(*minutes))


# --- The guard ---------------------------------------------------------------

HAIRCUT = str(uuid.uuid4())
KIDS = str(uuid.uuid4())


def _turn(text: str = "Sure.", *, intent: str = "UNKNOWN", sources: list[str] | None = None):
    return AgentTurn.model_validate(agent_turn(text, intent=intent, sources=sources))


def _found_haircut() -> Evidence:
    return Evidence(sources={HAIRCUT: "service"}, prices=(Decimal("40.00"),), durations=(45,))


def _faq(answer: str) -> FaqSnippet:
    return FaqSnippet(id=uuid.uuid4(), question="q", answer=answer, category=None)


def test_grounded_intents_are_the_fact_stating_ones():
    assert GROUNDED_INTENTS == {"FAQ", "BUSINESS_HOURS", "LOCATION", "PRICE_INQUIRY"}


def test_business_profile_is_always_citable_and_business_hours_only_when_listed():
    # The prompt's hours (10:00-16:00) also make their opening and closing times quotable.
    listed = TurnEvidence(hours_listed=True, hour_times={time(10, 0), time(16, 0)})
    unlisted = TurnEvidence(hours_listed=False)
    where = _turn("We're at 1 Main St.", intent="LOCATION", sources=[BUSINESS_PROFILE])
    hours = _turn("Open 10 to 4.", intent="BUSINESS_HOURS", sources=[" Business_Hours "])

    assert grounding_problem(where, unlisted) is None
    assert grounding_problem(hours, listed) is None
    assert grounding_problem(hours, unlisted) == "unknown_source"


@pytest.mark.parametrize(
    ("intent", "sources", "problem"),
    [
        ("PRICE_INQUIRY", [HAIRCUT], None),
        ("PRICE_INQUIRY", [], "missing_sources"),
        ("PRICE_INQUIRY", [BUSINESS_PROFILE], "unsupported_source"),
        ("BUSINESS_HOURS", [HAIRCUT], "unsupported_source"),
        ("LOCATION", [], "missing_sources"),
        ("LOCATION", [HAIRCUT], "unsupported_source"),
        ("FAQ", [BUSINESS_PROFILE], "unsupported_source"),
        # Not a fact-stating intent: any valid source is fine, and none is too.
        ("BOOK_APPOINTMENT", [HAIRCUT], None),
        ("UNKNOWN", [], None),
        ("UNKNOWN", [str(uuid.uuid4())], "unknown_source"),
    ],
)
def test_each_grounded_intent_needs_a_source_of_a_kind_that_backs_it(intent, sources, problem):
    evidence = TurnEvidence(hours_listed=True)
    evidence.add(_found_haircut())
    assert grounding_problem(_turn(intent=intent, sources=sources), evidence) == problem


def test_a_price_inquiry_may_cite_nothing_only_after_an_unresolved_lookup():
    evidence = TurnEvidence(hours_listed=False)
    honest = _turn("We don't list a perm, sorry.", intent="PRICE_INQUIRY")
    assert grounding_problem(honest, evidence) == "missing_sources"

    evidence.add(Evidence(unresolved=frozenset({"PRICE_INQUIRY"})))
    assert grounding_problem(honest, evidence) is None
    # The exemption is for PRICE_INQUIRY only, and never for an invented amount.
    assert grounding_problem(_turn("Sure.", intent="FAQ"), evidence) == "missing_sources"
    invented = _turn("We don't list it, but a perm is about $80.", intent="PRICE_INQUIRY")
    assert grounding_problem(invented, evidence) == "unverified_amount"


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("A haircut is $40.", None),
        ("A haircut is $40.00 and takes 45 minutes.", None),
        ("A haircut is forty dollars.", None),
        ("A haircut is $45.", "unverified_amount"),
        ("A haircut is $40, a kids cut $18.", "unverified_amount"),
        ("A haircut takes an hour.", "unverified_duration"),
        ("A haircut is $40 and takes 45 min.", None),
        ("A haircut is $40 and takes 3/4 of an hour.", "unverified_duration"),  # errs strict
    ],
)
def test_every_amount_must_match_a_retrieved_service(text, problem):
    evidence = TurnEvidence(hours_listed=False)
    evidence.add(_found_haircut())
    turn = _turn(text, intent="PRICE_INQUIRY", sources=[HAIRCUT])
    assert grounding_problem(turn, evidence) == problem


def test_amounts_in_an_faq_answer_retrieved_this_turn_are_accepted_whatever_the_intent():
    evidence = TurnEvidence(hours_listed=False)
    kids = _faq("Yes, children under 12 get a cut for 18 dollars, about 20 minutes.")
    evidence.add_faq(kids)

    ok = _turn("A kids cut is $18 and takes 20 min.", intent="FAQ", sources=[str(kids.id)])
    assert grounding_problem(ok, evidence) is None
    wrong = _turn("A kids cut is $20.", intent="FAQ", sources=[str(kids.id)])
    assert grounding_problem(wrong, evidence) == "unverified_amount"
    # Even with no fact-stating intent, an amount still has to come from somewhere.
    chatty = _turn("Our cheapest is $12!", intent="UNKNOWN")
    assert grounding_problem(chatty, evidence) == "unverified_amount"


def test_ambiguous_candidates_carry_no_prices_so_a_quoted_one_is_unverified():
    evidence = TurnEvidence(hours_listed=False)
    evidence.add(
        Evidence(
            sources={HAIRCUT: "service", KIDS: "service"},
            unresolved=frozenset({"PRICE_INQUIRY"}),
        )
    )
    ask = _turn("Did you mean a Haircut or a Kids cut?", intent="PRICE_INQUIRY")
    assert grounding_problem(ask, evidence) is None
    guess = _turn("A Haircut is $40.", intent="PRICE_INQUIRY", sources=[HAIRCUT])
    assert grounding_problem(guess, evidence) == "unverified_amount"


def test_business_hours_constant_matches_what_the_prompt_tells_the_model_to_cite():
    assert (BUSINESS_HOURS, BUSINESS_PROFILE) == ("business_hours", "business_profile")


def test_the_exemption_ends_once_a_service_was_found_this_turn():
    evidence = TurnEvidence(hours_listed=False)
    evidence.add(Evidence(unresolved=frozenset({"PRICE_INQUIRY"})))
    evidence.add(_found_haircut())
    vague = _turn("We don't list that one.", intent="PRICE_INQUIRY")
    assert grounding_problem(vague, evidence) == "missing_sources"


@pytest.mark.parametrize("intent", ["LOCATION", "BUSINESS_HOURS"])
def test_durations_in_location_and_hours_replies_are_not_service_durations(intent):
    evidence = TurnEvidence(hours_listed=True)
    source = "business_profile" if intent == "LOCATION" else "business_hours"
    for text in ("We're 5 minutes from the station.", "Please call 24 hours ahead."):
        assert grounding_problem(_turn(text, intent=intent, sources=[source]), evidence) is None
    # Money is still checked whatever the intent.
    priced = _turn("Parking is $5.", intent="LOCATION", sources=["business_profile"])
    assert grounding_problem(priced, evidence) == "unverified_amount"


def test_an_iso_code_glued_to_the_number_is_still_money():
    assert money_amounts("It's EUR40 or USD45.50.") == _d("40", "45.50")


# --- Clock-time detection ----------------------------------------------------


def _t(*readings: tuple[int, int]) -> frozenset[time]:
    return frozenset(time(h, m) for h, m in readings)


def _either(hour: int, minute: int = 0) -> frozenset[time]:
    """A 12-hour time without am/pm: the morning and the afternoon reading."""
    return _t((hour % 12, minute), (hour % 12 + 12, minute))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("2:30 PM", [_t((14, 30))], id="digits-pm"),
        pytest.param("14:30", [_t((14, 30))], id="24-hour"),
        pytest.param("2:30", [_either(2, 30)], id="digits-no-meridiem-both-readings"),
        pytest.param("2pm", [_t((14, 0))], id="hour-pm"),
        pytest.param("2 p.m.", [_t((14, 0))], id="dotted-pm"),
        pytest.param("12 pm", [_t((12, 0))], id="12-pm-is-noon"),
        pytest.param("12 am", [_t((0, 0))], id="12-am-is-midnight"),
        pytest.param("noon", [_t((12, 0))], id="noon"),
        pytest.param("midday", [_t((12, 0))], id="midday"),
        pytest.param("midnight", [_t((0, 0))], id="midnight"),
        pytest.param("two thirty", [_either(2, 30)], id="words-two-thirty"),
        pytest.param("four fifteen pm", [_t((16, 15))], id="words-with-pm"),
        pytest.param("three o'clock", [_either(3)], id="oclock"),
        pytest.param("half past two", [_either(2, 30)], id="half-past"),
        pytest.param("quarter past 3 pm", [_t((15, 15))], id="quarter-past-pm"),
        pytest.param("quarter to three", [_either(2, 45)], id="quarter-to"),
        pytest.param("quarter to one", [_either(12, 45)], id="quarter-to-one-wraps-to-12"),
        pytest.param("at 2", [_either(2)], id="bare-at-hour"),
        pytest.param("at two on Friday", [_either(2)], id="bare-at-word-before-day"),
        pytest.param(
            "We have 2:00 PM, 3:30 PM or 4 PM.",
            [_t((14, 0)), _t((15, 30)), _t((16, 0))],
            id="list-of-slots",
        ),
        pytest.param(
            "We open at 9:00 and close at 17:00.", [_either(9), _t((17, 0))], id="hours-sentence"
        ),
        pytest.param("Open 9 AM to 5 PM.", [_t((9, 0)), _t((17, 0))], id="am-pm-range"),
        pytest.param("2.30pm", [_t((14, 30))], id="dot-with-pm"),
        pytest.param("twenty past two", [_either(2, 20)], id="words-past"),
        pytest.param("ten to three pm", [_t((14, 50))], id="words-to-pm"),
        pytest.param("10 minutes to 4", [_either(3, 50)], id="digit-minutes-to"),
        pytest.param("Open from 9 until 5", [_either(9), _either(5)], id="bare-range-from"),
        pytest.param("We're open 9-5", [_either(9), _either(5)], id="bare-range-dash"),
        pytest.param("between 2 and 4:30", [_either(2), _either(4, 30)], id="between-range"),
        pytest.param("2-4 pm", [_t((14, 0)), _t((16, 0))], id="range-shares-pm"),
        pytest.param("open 9am-5pm", [_t((9, 0)), _t((17, 0))], id="range-own-meridiems"),
    ],
)
def test_clock_times_reads_each_time_as_its_possible_readings(text, expected):
    assert sorted(clock_times(text), key=sorted) == sorted(expected, key=sorted)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("$40.00", id="price"),
        pytest.param("$12.50", id="price-12-50"),
        pytest.param("12,50 €", id="euro-price"),
        pytest.param("90 minutes", id="duration"),
        pytest.param("1 hour 30 minutes", id="compound-duration"),
        pytest.param("+1 (312) 847-1928", id="phone"),
        pytest.param("We're at 1 Main St.", id="address-at"),
        pytest.param("We're at 5 Oak Ave", id="address-at-5"),
        pytest.param("12 Elm St", id="address-number"),
        pytest.param("2026-10-09", id="iso-date"),
        pytest.param("Friday the 10th", id="ordinal-date"),
        pytest.param("at 2 people", id="party-size"),
        pytest.param("two fifteen-minute slots", id="slot-count"),
        pytest.param("We have 2 openings", id="openings"),
        pytest.param("I am here", id="am-is-not-a-meridiem"),
        pytest.param("25:00", id="not-a-24-hour-time"),
        pytest.param("It takes 45 minutes, so 2 hours.", id="durations-sentence"),
        pytest.param("1:1 session", id="ratio"),
        pytest.param("a 2 hour slot", id="hour-slot-length"),
        pytest.param("7 days a week", id="days-a-week"),
        pytest.param("a 2-3 day wait", id="range-of-days"),
        pytest.param("ages 5 to 12", id="age-range"),
        pytest.param("$2.30 pm", id="price-then-pm"),
        pytest.param("2.5 hours", id="decimal-duration"),
        pytest.param("2-3 people", id="party-range"),
    ],
)
def test_clock_times_ignores_what_is_not_a_time_of_day(text):
    assert clock_times(text) == []


def test_each_time_is_read_once():
    assert len(clock_times("See you at 2:30 PM, or at 3 pm.")) == 2


# --- Clock times in the guard --------------------------------------------------


def test_only_offered_slot_times_may_be_stated():
    evidence = TurnEvidence(hours_listed=True)
    evidence.add(Evidence(slot_times=_t((14, 0), (15, 30))))

    ok = _turn("I have 2:00 PM or 3:30 PM.", intent="BOOK_APPOINTMENT")
    assert grounding_problem(ok, evidence) is None
    wrong = _turn("I have 4:00 PM.", intent="BOOK_APPOINTMENT")
    assert grounding_problem(wrong, evidence) == UNVERIFIED_TIME == "unverified_time"
    # One good time does not excuse a bad one.
    mixed = _turn("I have 2 PM or 5 PM.", intent="BOOK_APPOINTMENT")
    assert grounding_problem(mixed, evidence) == "unverified_time"
    # A reply with no time at all is fine.
    assert grounding_problem(_turn("Which day suits you?"), evidence) is None


def test_an_ambiguous_time_passes_when_either_reading_is_offered():
    evidence = TurnEvidence(hours_listed=False)
    evidence.add(Evidence(slot_times=_t((14, 30))))
    assert grounding_problem(_turn("How about 2:30?"), evidence) is None
    assert grounding_problem(_turn("How about two thirty?"), evidence) is None
    assert grounding_problem(_turn("How about 3:30?"), evidence) == "unverified_time"


def test_a_time_with_nothing_offered_and_no_hours_is_unverified():
    evidence = TurnEvidence(hours_listed=False)
    assert grounding_problem(_turn("See you at 2 PM."), evidence) == "unverified_time"


def test_a_non_booking_intent_may_state_hours_when_nothing_was_offered():
    evidence = TurnEvidence(hours_listed=True, hour_times=_t((9, 0), (17, 0)))
    evidence.add(Evidence(hour_times=_t((10, 0))))
    sources = [BUSINESS_HOURS]

    both = _turn("We open at 9 AM and close at 5 PM.", intent="BUSINESS_HOURS", sources=sources)
    assert grounding_problem(both, evidence) is None
    later = _turn("Saturday we open at 10:00 AM.", intent="BUSINESS_HOURS", sources=sources)
    assert grounding_problem(later, evidence) is None
    wrong = _turn("We close at 6 PM.", intent="BUSINESS_HOURS", sources=sources)
    assert grounding_problem(wrong, evidence) == "unverified_time"


@pytest.mark.parametrize("intent", ["BOOK_APPOINTMENT", "RESCHEDULE"])
def test_booking_intents_may_never_state_hour_or_faq_times(intent):
    evidence = TurnEvidence(hours_listed=True, hour_times=_t((9, 0), (17, 0)))
    evidence.add_faq(_faq("Walk-ins are welcome until 3 PM."))
    for text in ("We open at 9 AM.", "We close at 5 PM.", "Walk-ins until 3 PM."):
        assert grounding_problem(_turn(text, intent=intent), evidence) == "unverified_time", text


def test_a_non_booking_intent_may_state_a_time_from_a_retrieved_faq():
    evidence = TurnEvidence(hours_listed=False)
    snippet = _faq("Walk-ins are welcome until 3 PM.")
    evidence.add_faq(snippet)
    ok = _turn("Walk-ins are welcome until 3 PM.", intent="FAQ", sources=[str(snippet.id)])
    assert grounding_problem(ok, evidence) is None
    other = _turn("Walk-ins are welcome until 4 PM.", intent="FAQ", sources=[str(snippet.id)])
    assert grounding_problem(other, evidence) == "unverified_time"


def test_a_faq_time_is_not_allowed_before_that_faq_was_retrieved():
    evidence = TurnEvidence(hours_listed=False)
    reply = _turn("Walk-ins are welcome until 3 PM.", intent="UNKNOWN")
    assert grounding_problem(reply, evidence) == "unverified_time"


@pytest.mark.parametrize("intent", ["BUSINESS_HOURS", "FAQ", "UNKNOWN", "BOOK_APPOINTMENT"])
def test_once_slots_are_offered_hour_and_faq_times_are_no_longer_allowed(intent):
    evidence = TurnEvidence(hours_listed=True, hour_times=_t((9, 0), (17, 0)))
    snippet = _faq("Walk-ins are welcome until 3 PM.")
    evidence.add_faq(snippet)
    evidence.add(Evidence(slot_times=_t((14, 0))))
    sources = {"BUSINESS_HOURS": [BUSINESS_HOURS], "FAQ": [str(snippet.id)]}.get(intent, [])

    opening = _turn("We open at 9 AM.", intent=intent, sources=sources)
    assert grounding_problem(opening, evidence) == "unverified_time"
    from_faq = _turn("Until 3 PM.", intent=intent, sources=sources)
    assert grounding_problem(from_faq, evidence) == "unverified_time"
    # The offered slot itself still passes.
    assert grounding_problem(_turn("2 PM works.", intent=intent, sources=sources), evidence) is None


def test_the_amount_check_runs_before_the_time_check():
    evidence = TurnEvidence(hours_listed=False)
    reply = _turn("A cut is $99 at 2 PM.")
    assert grounding_problem(reply, evidence) == "unverified_amount"


# --- Booking claims (slice #11) ----------------------------------------------

CLAIMS = [
    ("You're booked for a haircut Friday at 3:30 PM.", ["booking"]),
    ("You're all set!", ["booking"]),
    ("Great news, Alex, you are now booked in.", ["booking"]),
    ("I've booked you in for Friday.", ["booking"]),
    ("I have gone ahead and scheduled a haircut for you.", ["booking"]),
    ("Your haircut appointment is confirmed.", ["booking"]),
    ("I have you down for 3:30.", ["booking"]),
    ("Confirmed for Friday at 3:30.", ["booking"]),
    ("That's confirmed.", ["booking"]),
    ("Your appointment has been cancelled.", ["change"]),
    ("I've moved your appointment to Friday.", ["change"]),
    ("You've been rescheduled.", ["change"]),
    ("I've booked a haircut for you on Friday.", ["booking"]),
    ("I've booked it for Friday.", ["booking"]),
    ("I have booked a Friday appointment for you.", ["booking"]),
    ("You're booked with Maria for Friday.", ["booking"]),
    ("So you're all set for Friday.", ["booking"]),
    # A claim followed by a question is still a claim.
    ("You're all set for Friday at 3:30 PM — is there anything else I can help with?", ["booking"]),
    ("You're booked for Friday - anything else?", ["booking"]),
    ("You're booked, anything else?", ["booking"]),
    ("Great, I've booked you in; want a reminder?", ["booking"]),
]
NOT_CLAIMS = [
    "Shall I book that for you?",
    "Want me to get you booked?",
    "Shall I have you down for 3:30?",
    "Do you want me to confirm the 3:30?",
    "Sorry, that time is already booked. I have 3:45 or 4:00.",
    "It's booked up that afternoon.",
    "Booked solid on Friday, sorry.",
    "Our stylists are fully booked on Saturday.",
    "Once you're booked you'll get a text.",
    "You're not booked yet.",
    "Your appointment isn't confirmed yet.",
    "I couldn't complete the booking, so nothing is booked.",
    "We have reserved parking for customers.",
    "We've moved to 12 Main St.",
    "Your request to cancel has been noted; the team will help.",
    "I've confirmed that we're open until 5.",
    "I've confirmed this with the calendar.",
    "I've confirmed it with the team.",
    "You're all set with that question.",
    "You're confirmed as the contact.",
]


@pytest.mark.parametrize(("text", "kinds"), CLAIMS, ids=[f"claim-{i}" for i in range(len(CLAIMS))])
def test_booking_claims_are_detected(text, kinds):
    assert booking_claims(text) == kinds


@pytest.mark.parametrize("text", NOT_CLAIMS, ids=[f"not-{i}" for i in range(len(NOT_CLAIMS))])
def test_questions_offers_and_taken_slots_are_not_claims(text):
    assert booking_claims(text) == []


@pytest.mark.parametrize("intent", ["BOOK_APPOINTMENT", "UNKNOWN", "HUMAN_REQUEST"])
def test_a_booking_claim_needs_a_confirmed_booking_this_turn(intent):
    evidence = TurnEvidence(hours_listed=False)
    reply = _turn("You're all set!", intent=intent)
    assert grounding_problem(reply, evidence) == UNCONFIRMED_BOOKING

    evidence.add(Evidence(booking_confirmed=True))
    assert grounding_problem(reply, evidence) is None


def test_a_cancel_claim_is_never_grounded():
    evidence = TurnEvidence(hours_listed=False)
    evidence.add(Evidence(booking_confirmed=True))
    reply = _turn("Done, your appointment has been cancelled.", intent="CANCEL")
    assert grounding_problem(reply, evidence) == UNCONFIRMED_BOOKING


def test_the_booked_time_is_the_offer_for_the_time_check():
    evidence = TurnEvidence(hours_listed=False, earlier_offer_times=_t((14, 0), (15, 30)))
    evidence.add(Evidence(slot_times=_t((15, 30)), booking_confirmed=True))
    assert grounding_problem(_turn("You're booked for 3:30 PM."), evidence) is None
    assert grounding_problem(_turn("You're booked for 2 PM."), evidence) == UNVERIFIED_TIME


# --- Callback promises (slice #13) -------------------------------------------

CALLBACK_PROMISES = [
    "We'll call you back this afternoon.",
    "Someone will call you tomorrow.",
    "I will give you a call shortly.",
    "I'll get back to you today.",
    "We will get back to you within 24 hours.",
    "The team will contact you soon.",
    "Someone will reach out to you by Friday.",
    "Expect a call from us in an hour.",
    "A manager is going to phone you.",
    "We'll be in touch.",
    "I'll text you back.",
    "Thanks for your patience - the owner will also call you about this.",
    "We’ll call you back.",
    "If you need anything else, we'll call you back.",
]
NOT_CALLBACK_PROMISES = [
    "Would you like the team's number?",
    "Shall we call you back?",
    "If you'd like us to call you back, tell me and I'll note it.",
    "You can call them back at 555-0100.",
    "Please call us at 555-0100 and the team will help.",
    "I've let the team know; they will review this.",
    "The team will be notified and will review your request.",
    "I can't promise a callback, but you can call the shop directly.",
    "We won't be able to call you back, sorry.",
    "Feel free to reach out to us anytime.",
    "You can bring your kids in, we ring the bell when ready.",
    "I'll note your request.",
]


@pytest.mark.parametrize("text", CALLBACK_PROMISES)
def test_a_promised_callback_is_a_problem_whatever_the_intent(text):
    for intent in ("UNKNOWN", "HUMAN_REQUEST", "COMPLAINT"):
        reply = _turn(text, intent=intent)
        assert grounding_problem(reply, TurnEvidence(hours_listed=False)) == PROMISED_CALLBACK


@pytest.mark.parametrize("text", NOT_CALLBACK_PROMISES)
def test_offers_questions_and_the_customer_calling_are_not_callback_promises(text):
    reply = _turn(text, intent="HUMAN_REQUEST")
    assert grounding_problem(reply, TurnEvidence(hours_listed=False)) is None


def test_a_callback_promise_wins_over_the_amount_and_time_checks_it_carries():
    reply = _turn("We'll call you back within 24 hours.", intent="UNKNOWN")
    assert grounding_problem(reply, TurnEvidence(hours_listed=False)) == PROMISED_CALLBACK


def test_the_callback_correction_names_what_to_say_instead():
    assert "callback" in CALLBACK_CORRECTION and "notified" in CALLBACK_CORRECTION


@pytest.mark.parametrize(
    "text",
    [
        "I've let the team know.",
        "I have let the owner know about this.",
        "I'll pass this on to the team.",
        "Let me pass that along to the owner.",
        "The team will be notified.",
        "The owner has been informed.",
        "I've flagged this for the team.",
        "I've notified the owner.",
        "We have already alerted the staff.",
        "This has been flagged for the team.",
        "I'll let the team know right away.",
        "Sorry about that - I've passed this on to the manager.",
        "I just told the team about it.",
    ],
)
def test_team_notified_claims(text):
    assert claims_team_notified(text)


@pytest.mark.parametrize(
    "text",
    [
        "Would you like me to let the team know?",
        "Would you like me to let the team know.",
        "You can let the team know by calling 555-0100.",
        "Please let the team know your name.",
        "I can't notify the team from here.",
        "I won't be able to let the team know.",
        "I haven't notified the owner.",
        "If you'd like, I can pass this on to the team.",
        "Shall I let the team know?",
        "Once you're booked the team will be notified of nothing else.",
        "We are open from 9 to 5.",
        "The team is available weekdays.",
        "",
    ],
)
def test_not_team_notified_claims(text):
    assert not claims_team_notified(text)
