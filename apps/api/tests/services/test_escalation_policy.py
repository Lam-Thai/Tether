"""`escalation_policy` at its boundary: pure rules, no DB, no model."""

import pytest

from services.escalation_policy import (
    EMERGENCY_REPLY,
    REASONS,
    REPEATED_FAILURES,
    EscalationDecision,
    EscalationRules,
    OperationFact,
    TurnSignals,
    evaluate,
    needs_emergency_pointer,
)

RULES = EscalationRules()


def _turn(
    text: str = "",
    intent: str | None = "FAQ",
    *,
    model_requires_human: bool = False,
    fallback: bool = False,
) -> TurnSignals:
    return TurnSignals(
        customer_text=text,
        intent=intent,
        model_requires_human=model_requires_human,
        fallback=fallback,
    )


def _reason(text="", intent: str | None = "FAQ", state=None, operations=(), rules=RULES, **kwargs):
    decision = evaluate(state or {}, _turn(text, intent, **kwargs), operations, rules)
    return decision.reason if decision else None


# --- Urgent -------------------------------------------------------------------

URGENT_TEXTS = [
    "This is an emergency, my friend collapsed",
    "it's a medical emergency",
    "I can't breathe properly after the treatment",
    "she cannot breathe",
    "I have chest pain",
    "He is bleeding heavily",
    "my dad is unconscious",
    "I think I'm having an allergic reaction to the dye",
    "there's a fire in the kitchen next door",
    "I took an overdose",
    "I want to kill myself",
    "i might hurt myself",
    "I’m having trouble breathing",
    "she is unresponsive",
    "my client went unresponsive in the chair",
    "help, the kitchen is on fire",
    "the dryer is on fire",
]


@pytest.mark.parametrize("text", URGENT_TEXTS)
def test_a_safety_phrase_is_urgent_with_high_urgency_whatever_the_intent(text):
    decision = evaluate({}, _turn(text, "FAQ"), (), RULES)
    assert decision == EscalationDecision("urgent", "high", ("safety_phrase",))


def test_the_urgent_intent_alone_is_urgent():
    decision = evaluate({}, _turn("help", "URGENT"), (), RULES)
    assert decision == EscalationDecision("urgent", "high", ("intent:URGENT",))


def test_every_rule_that_fired_is_a_signal_and_the_model_flag_is_added():
    decision = evaluate(
        {}, _turn("emergency? I can't breathe", "URGENT", model_requires_human=True), (), RULES
    )
    assert decision is not None
    assert decision.signals == ("intent:URGENT", "safety_phrase", "model_flag")


@pytest.mark.parametrize(
    "text",
    [
        "Can I get a fire engine red colour?",
        "Let's fire up the dryer",
        "It's not an emergency, just a question about hair",
        "No emergency here, what time do you close?",
        "I'll breathe easy once my cut is done",
        "I passed by your shop yesterday",
        "My hair is on point",
        "Do you take emergency walk-ins?",
        "Your booking page is unresponsive",
        "the card reader was unresponsive yesterday",
        "that red colour is on fire",
        "my look is on fire, thanks",
    ],
)
def test_ordinary_wording_near_a_safety_word_is_not_urgent(text):
    assert _reason(text) is None


def test_owner_keywords_match_whole_words_and_phrases_only():
    rules = EscalationRules(urgent_keywords=("lice", "burn", "scalp  infection"))
    assert _reason("I think I have lice", rules=rules) == "urgent"
    assert _reason("the dye gave me a Burn!", rules=rules) == "urgent"
    assert _reason("a scalp infection, maybe", rules=rules) == "urgent"
    assert _reason("Do you sell police-style jackets? I'm so slice of life", rules=rules) is None
    assert _reason("burnt orange please", rules=rules) is None
    signals = evaluate({}, _turn("lice", "FAQ"), (), rules)
    assert signals is not None and signals.signals == ("owner_keyword",)


def test_blank_owner_keywords_are_ignored():
    assert _reason("anything", rules=EscalationRules(urgent_keywords=("", "  "))) is None


# --- Human requested ----------------------------------------------------------

HUMAN_TEXTS = [
    "I want to speak to a real person",
    "can I talk to a human please",
    "let me speak with the manager",
    "I need to talk to someone",
    "get me the owner",
    "Is there a representative I can reach?",
    "transfer me to someone",
    "Can someone call me back?",
    "Please have the owner call me",
    "is this a bot? i want a human",
]


@pytest.mark.parametrize("text", HUMAN_TEXTS)
def test_asking_for_a_person_is_human_requested(text):
    decision = evaluate({}, _turn(text, "FAQ"), (), RULES)
    assert decision is not None
    assert (decision.reason, decision.urgency) == ("human_requested", "normal")


def test_the_human_request_intent_alone_is_enough():
    decision = evaluate({}, _turn("hmm", "HUMAN_REQUEST"), (), RULES)
    assert decision == EscalationDecision("human_requested", "normal", ("intent:HUMAN_REQUEST",))


@pytest.mark.parametrize(
    "text",
    [
        "No need for a real person, you've been great",
        "I don't need to talk to a person, just the hours",
        "I don't want to speak to a human, you're fine",
        "I'd rather not talk to anyone, just book me",
        "is the manager's special still on?",
        "Who is your manager? Just curious",
        "thanks, that's all",
        "The owner's name is on the sign",
        "I'm a human, I swear",
    ],
)
def test_negated_or_incidental_mentions_do_not_ask_for_a_person(text):
    assert _reason(text) is None


def test_a_negation_in_an_earlier_clause_does_not_cancel_a_later_request():
    assert _reason("I don't need a price. Let me talk to a real person") == "human_requested"
    assert _reason("no, I want to speak to the manager") == "human_requested"


# --- Complaint ----------------------------------------------------------------

COMPLAINT_TEXTS = [
    "I want to make a complaint",
    "my cut came out wrong",
    "You ruined my hair",
    "I want a refund",
    "I'm very unhappy with the colour",
    "I am not happy with the service",
    "That was terrible service",
    "I want my money back",
    "This is unacceptable",
]


@pytest.mark.parametrize("text", COMPLAINT_TEXTS)
def test_complaint_wording_is_a_complaint(text):
    assert _reason(text) == "complaint"


@pytest.mark.parametrize(
    "text",
    [
        "my hair came out great",
        "No complaints at all, thanks",
        "What is your refund policy?",
        "I'm happy with the colour",
        "Is the service menu online?",
    ],
)
def test_praise_and_policy_questions_are_not_complaints(text):
    assert _reason(text) is None


def test_the_complaint_intent_alone_is_enough():
    assert _reason("hmm", "COMPLAINT") == "complaint"


# --- Change request, booking, integrations, clarification ----------------------


@pytest.mark.parametrize("intent", ["RESCHEDULE", "CANCEL"])
def test_changing_or_cancelling_is_a_change_request(intent):
    decision = evaluate({}, _turn("move my appointment", intent), (), RULES)
    assert decision == EscalationDecision("change_request", "normal", (f"intent:{intent}",))


def test_a_failed_booking_anywhere_in_the_interaction_is_booking_unresolved():
    ops = [
        OperationFact("check_availability", "succeeded", "offered"),
        OperationFact("create_booking", "succeeded", "booking_failed"),
    ]
    decision = evaluate({}, _turn("ok", "BOOK_APPOINTMENT"), ops, RULES)
    assert decision == EscalationDecision("booking_unresolved", "normal", ("booking_failed",))


def test_other_booking_results_do_not_escalate():
    ops = [
        OperationFact("create_booking", "succeeded", "slot_taken"),
        OperationFact("create_booking", "rejected", None),
        OperationFact("create_booking", "succeeded", "confirmed"),
    ]
    assert evaluate({}, _turn("ok", "BOOK_APPOINTMENT"), ops, RULES) is None


def test_the_repeat_threshold_is_two():
    assert REPEATED_FAILURES == 2


def test_the_same_tool_failing_twice_is_integration_failing():
    ops = [
        OperationFact("check_availability", "succeeded", "calendar_unavailable"),
        OperationFact("get_services", "succeeded", "listed"),
        OperationFact("check_availability", "succeeded", "calendar_unavailable"),
    ]
    decision = evaluate({}, _turn("ok", "BOOK_APPOINTMENT"), ops, RULES)
    assert decision == EscalationDecision(
        "integration_failing", "normal", ("repeated_failure:check_availability",)
    )


def test_failed_status_rows_count_and_one_failure_does_not_escalate():
    one = [OperationFact("search_faq", "failed", None)]
    assert evaluate({}, _turn("ok"), one, RULES) is None
    two = [*one, OperationFact("search_faq", "failed", None)]
    assert _reason(operations=two) == "integration_failing"


def test_different_tools_failing_once_each_is_not_a_pattern():
    ops = [
        OperationFact("search_faq", "failed", None),
        OperationFact("check_availability", "succeeded", "calendar_unavailable"),
    ]
    assert evaluate({}, _turn("ok"), ops, RULES) is None


def test_repeated_provider_failures_in_state_are_integration_failing():
    failures = [{"kind": "provider_unavailable"}, {"kind": "invalid_output"}]
    assert _reason(state={"failures": failures}) is None
    failures.append({"kind": "provider_unavailable", "detail": "turn_timeout"})
    decision = evaluate({"failures": failures}, _turn("ok"), (), RULES)
    assert decision is not None and decision.signals == ("repeated_failure:llm",)


def test_clarification_attempts_reach_the_owners_threshold():
    assert _reason(state={"clarification_attempts": 1}, intent=None, fallback=True) is None
    assert (
        _reason(state={"clarification_attempts": 2}, intent=None, fallback=True)
        == "clarification_exhausted"
    )
    strict = EscalationRules(clarification_threshold=1)
    assert _reason(state={"clarification_attempts": 1}, rules=strict) == "clarification_exhausted"
    lenient = EscalationRules(clarification_threshold=4)
    assert _reason(state={"clarification_attempts": 3}, rules=lenient) is None


def test_the_models_requires_human_flag_alone_never_escalates():
    assert _reason("what is the wifi password", model_requires_human=True) is None


# --- Priority -------------------------------------------------------------------


def test_the_highest_priority_reason_wins_and_all_signals_are_kept():
    ops = [OperationFact("create_booking", "succeeded", "booking_failed")]
    state = {"clarification_attempts": 5}
    decision = evaluate(state, _turn("I want a refund and a real person", "RESCHEDULE"), ops, RULES)
    assert decision is not None
    assert decision.reason == "human_requested"
    assert decision.signals == (
        "human_phrase",
        "complaint_phrase",
        "intent:RESCHEDULE",
        "booking_failed",
        "clarification_attempts",
    )


@pytest.mark.parametrize(
    ("higher", "lower"),
    [(a, b) for i, a in enumerate(REASONS) for b in REASONS[i + 1 :]],
)
def test_reasons_are_ordered_by_priority(higher, lower):
    assert REASONS.index(higher) < REASONS.index(lower)


def test_ordinary_turns_do_not_escalate():
    for text, intent in [
        ("what are your hours on Saturday", "BUSINESS_HOURS"),
        ("how much is a haircut", "PRICE_INQUIRY"),
        ("I'd like a haircut tomorrow at 2", "BOOK_APPOINTMENT"),
        ("thanks, that's all", "UNKNOWN"),
        ("", None),
    ]:
        assert _reason(text, intent) is None


def test_the_decision_never_carries_customer_text():
    decision = evaluate({}, _turn("my number is 555-0100, I want a refund"), (), RULES)
    assert decision is not None and "555" not in repr(decision)


# --- Emergency pointer ----------------------------------------------------------

URGENT = EscalationDecision("urgent", "high", ("safety_phrase",))
CALM = EscalationDecision("complaint", "normal", ("complaint_phrase",))


@pytest.mark.parametrize(
    "reply",
    [
        "Please call 911 right away.",
        "Contact your local emergency services immediately.",
        "Call emergency services now. I've notified the team.",
        "Please go to the nearest emergency room.",
        "Dial 112 or your local number.",
        EMERGENCY_REPLY,
    ],
)
def test_an_urgent_reply_that_points_to_emergency_services_is_kept(reply):
    assert not needs_emergency_pointer(URGENT, reply)


@pytest.mark.parametrize(
    "reply",
    [
        "I'm so sorry to hear that. The team will be notified.",
        "Try to stay calm. Our number is 555-0100.",
        "",
    ],
)
def test_an_urgent_reply_without_a_pointer_is_replaced(reply):
    assert needs_emergency_pointer(URGENT, reply)


def test_only_urgent_decisions_need_the_pointer():
    assert not needs_emergency_pointer(None, "Sorry to hear that.")
    assert not needs_emergency_pointer(CALM, "Sorry to hear that.")


@pytest.mark.parametrize(
    ("signals", "expected"),
    [
        (("safety_phrase",), True),
        (("intent:URGENT",), True),
        (("owner_keyword", "safety_phrase"), True),
        (("owner_keyword",), False),
        ((), False),
    ],
    ids=["safety", "intent", "both", "owner-keyword-only", "no-signals"],
)
def test_the_emergency_pointer_is_only_for_safety_origin_urgency(signals, expected):
    decision = EscalationDecision("urgent", "high", signals)
    assert needs_emergency_pointer(decision, "Noted, thanks.") is expected
