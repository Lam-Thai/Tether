"""The built-in scorers, on hand-built transcripts."""

from dataclasses import replace

import pytest

from evals.cases import EvalCase
from evals.playback import Operation, PlayedTurn
from evals.scorers import SCORERS

FAQ_ID = "8b0f6c2e-0000-4000-8000-000000000001"
SERVICE_ID = "8b0f6c2e-0000-4000-8000-000000000002"

SEARCH = Operation("search_faq", "succeeded", {"faq_ids": [FAQ_ID], "count": 1})
SERVICES = Operation("get_services", "succeeded", {"match": "found", "service_ids": [SERVICE_ID]})

ANSWER = PlayedTurn(
    customer="can I walk in?",
    reply="Yes, walk-ins before 3 PM.",
    intent="FAQ",
    model_intent="FAQ",
    sources=(FAQ_ID,),
    fallback=None,
    operations=(SEARCH,),
    model_calls=2,
    rate_limited=False,
)
NO_INFO = replace(
    ANSWER,
    reply="Sorry, I don't have that information.",
    intent="UNKNOWN",
    sources=(),
    fallback="no_info",
)


def case(**fields) -> EvalCase:
    return EvalCase(id="c", turns=("can I walk in?",), **fields)


def score(name: str, test_case: EvalCase, *turns: PlayedTurn):
    return SCORERS[name].score(test_case, turns, [op for t in turns for op in t.operations])


class TestIntentMatch:
    def test_compares_the_last_turns_model_intent(self):
        assert score("intent_match", case(expected_intent="FAQ"), ANSWER).passed
        missed = score("intent_match", case(expected_intent="LOCATION"), ANSWER)
        assert not missed.passed and missed.detail == "expected LOCATION, got FAQ"

    def test_falls_back_to_the_engine_intent_when_no_reply_was_valid(self):
        invalid = replace(ANSWER, model_intent=None, intent="UNKNOWN", fallback="invalid_output")
        assert not score("intent_match", case(expected_intent="FAQ"), invalid).passed
        assert score("intent_match", case(expected_intent="UNKNOWN"), invalid).passed


class TestGroundedInFaq:
    answerable = case(must_contain=("3",))
    unanswerable = case(must_not_contain=("we validate",))

    def test_answer_citing_a_retrieved_faq_with_the_fact_passes(self):
        assert score("grounded_in_faq", self.answerable, ANSWER).passed

    def test_a_service_returned_this_turn_also_grounds_it(self):
        turn = replace(ANSWER, sources=(SERVICE_ID,), operations=(SERVICES,))
        assert score("grounded_in_faq", self.answerable, turn).passed

    def test_citing_an_id_no_tool_returned_fails(self):
        turn = replace(ANSWER, sources=(FAQ_ID, "made-up-id"))
        result = score("grounded_in_faq", self.answerable, turn)
        assert not result.passed and "made-up-id" in result.detail

    def test_an_answer_citing_nothing_retrieved_fails(self):
        turn = replace(ANSWER, sources=("business_profile",))
        result = score("grounded_in_faq", self.answerable, turn)
        assert not result.passed and "cites no retrieved" in result.detail

    def test_a_failed_search_grounds_nothing(self):
        turn = replace(ANSWER, operations=(replace(SEARCH, status="failed"),))
        assert not score("grounded_in_faq", self.answerable, turn).passed

    def test_missing_and_forbidden_phrases_fail(self):
        result = score(
            "grounded_in_faq", case(must_contain=("24",), must_not_contain=("walk-ins",)), ANSWER
        )
        assert not result.passed
        assert "missing ['24']" in result.detail and "contains ['walk-ins']" in result.detail

    @pytest.mark.parametrize(
        ("reply", "states_3"),
        [
            ("Walk-ins before 3 PM.", True),
            ("Walk-ins until 3pm.", True),
            ("It's $3.", True),
            ("We hold your slot for 30 minutes.", False),
            ("Walk-ins until 1:30.", False),
            ("Walk-ins until 13:00.", False),
            ("About 3.5 hours.", False),
        ],
    )
    def test_numbers_match_whole_numbers_only(self, reply, states_3):
        result = score("grounded_in_faq", self.answerable, replace(ANSWER, reply=reply))
        assert result.passed is states_3, result.detail

    def test_words_may_take_a_suffix(self):
        turn = replace(ANSWER, reply="Yes, we do highlights and balayage.")
        assert score("grounded_in_faq", case(must_contain=("highlight",)), turn).passed

    def test_no_info_fails_an_answerable_question_and_passes_an_unanswerable_one(self):
        assert not score("grounded_in_faq", self.answerable, NO_INFO).passed
        assert score("grounded_in_faq", self.unanswerable, NO_INFO).passed

    def test_an_unanswerable_question_fails_on_an_invented_answer(self):
        invented = replace(ANSWER, reply="Yes, we validate parking!", sources=())
        assert not score("grounded_in_faq", self.unanswerable, invented).passed
        honest = replace(ANSWER, reply="I'm not sure about parking validation.", sources=())
        assert score("grounded_in_faq", self.unanswerable, honest).passed

    @pytest.mark.parametrize("kind", ["provider", "invalid_output", "quota"])
    def test_any_other_fallback_fails(self, kind):
        turn = replace(NO_INFO, fallback=kind)
        result = score("grounded_in_faq", self.unanswerable, turn)
        assert not result.passed and kind in result.detail


class TestNoFalseConfirmation:
    @pytest.mark.parametrize(
        "reply",
        [
            "You're all set for Saturday at 10!",
            "I've booked you in for a haircut.",
            "Your appointment is confirmed.",
            "Done, it’s cancelled.",
            "Your appointment has been rescheduled to Friday.",
            "I've gone ahead and booked you for 3pm.",
            "Booked for 3pm! See you then.",
            "Great. Booked for Friday.",
            "I have you down for Friday at 10.",
            "Your booking was made for Saturday.",
            "We've just sent you a confirmation text.",
        ],
    )
    def test_a_claim_without_a_confirming_tool_fails(self, reply):
        turn = replace(ANSWER, reply=reply, operations=())
        assert not score("no_false_confirmation", case(), turn).passed

    @pytest.mark.parametrize(
        "reply",
        [
            "I'll note your request and the team will be in touch.",
            "Would you like me to note a booking request for Saturday?",
            "Is that confirmed on your side?",
            "Want me to get you booked for 3pm?",
            "Shall I book it for you?",
        ],
    )
    def test_offers_and_questions_pass(self, reply):
        assert score("no_false_confirmation", case(), replace(ANSWER, reply=reply)).passed

    def test_a_claim_backed_by_a_confirming_tool_that_turn_passes(self):
        booked = Operation("create_booking", "succeeded", {"booking_id": "b1"})
        turn = replace(ANSWER, reply="You're all set!", operations=(booked,))
        assert score("no_false_confirmation", case(), turn).passed
        failed = replace(turn, operations=(replace(booked, status="failed"),))
        assert not score("no_false_confirmation", case(), failed).passed

    def test_a_claim_needs_the_tool_that_does_that_thing(self):
        booked = Operation("create_booking", "succeeded", {"booking_id": "b1"})
        turn = replace(ANSWER, reply="Your appointment is cancelled.", operations=(booked,))
        result = score("no_false_confirmation", case(), turn)
        assert not result.passed and "cancelled" in result.detail
        escalated = Operation("request_escalation", "succeeded", {})
        turn = replace(ANSWER, reply="You're all set!", operations=(escalated,))
        assert not score("no_false_confirmation", case(), turn).passed


def test_tools_used_needs_each_expected_tool_to_succeed():
    expects = case(expected_tools=("search_faq", "get_services"))
    assert not score("tools_used", expects, ANSWER).passed
    both = replace(ANSWER, operations=(SEARCH, SERVICES))
    assert score("tools_used", expects, both).passed
