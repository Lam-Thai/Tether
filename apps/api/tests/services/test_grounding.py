"""The grounding guard at its boundary: amount detection and `TurnEvidence.problem`. Pure."""

import uuid
from decimal import Decimal

import pytest

from integrations.llm.fake import agent_turn
from schemas.agent_turn import AgentTurn
from services.grounding import (
    BUSINESS_HOURS,
    BUSINESS_PROFILE,
    GROUNDED_INTENTS,
    Evidence,
    TurnEvidence,
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
    listed, unlisted = TurnEvidence(hours_listed=True), TurnEvidence(hours_listed=False)
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
