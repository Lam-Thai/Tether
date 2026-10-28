"""`TwimlRenderer`, `step_for` and `is_goodbye`: pure, no DB."""

import uuid
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

import pytest

from services.conversation import MessageView, TurnResult
from services.twiml import (
    GOODBYE,
    MAX_SAY_CHARS,
    Close,
    Hold,
    Listen,
    TwimlRenderer,
    VoiceUrls,
    is_goodbye,
    step_for,
)

INPUT = "https://api.example.test/api/v1/webhooks/twilio/voice/input"
WAIT = "https://api.example.test/api/v1/webhooks/twilio/voice/wait"
FALLBACK_LINE = "Sorry, could you say that again?"


def _parse(xml: str) -> ElementTree.Element:
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    return ElementTree.fromstring(xml.split("?>", 1)[1])


def _renderer(input_url: str = INPUT, wait_url: str = WAIT) -> TwimlRenderer:
    return TwimlRenderer(VoiceUrls(input=input_url, wait=wait_url))


def test_listen_gathers_speech_and_posts_to_the_input_url():
    root = _parse(_renderer().render(Listen("How can I help?", turn=3)))

    assert root.tag == "Response"
    assert [c.tag for c in root] == ["Gather"]
    gather = root[0]
    assert gather.attrib["input"] == "speech"
    assert gather.attrib["method"] == "POST"
    assert gather.attrib["actionOnEmptyResult"] == "true"
    assert gather.attrib["speechTimeout"] == "auto"
    action = urlparse(gather.attrib["action"])
    assert f"{action.scheme}://{action.netloc}{action.path}" == INPUT
    assert parse_qs(action.query) == {"turn": ["3"]}
    assert [c.tag for c in gather] == ["Say"]
    assert gather[0].text == "How can I help?"


def test_listen_carries_silences_only_when_positive():
    quiet = _parse(_renderer().render(Listen("Hello?", turn=2, silences=2)))
    query = parse_qs(urlparse(quiet[0].attrib["action"]).query)
    assert query == {"turn": ["2"], "silences": ["2"]}

    none = _parse(_renderer().render(Listen("Hello?", turn=2, silences=0)))
    assert "silences" not in none[0].attrib["action"]


def test_hold_says_then_redirects_to_the_wait_url():
    root = _parse(_renderer().render(Hold("One moment.", turn=4, attempt=2)))

    assert [c.tag for c in root] == ["Say", "Redirect"]
    assert root[0].text == "One moment."
    assert root[1].attrib["method"] == "POST"
    url = urlparse(root[1].text or "")
    assert f"{url.scheme}://{url.netloc}{url.path}" == WAIT
    assert parse_qs(url.query) == {"turn": ["4"], "attempt": ["2"]}


def test_close_says_then_hangs_up():
    root = _parse(_renderer().render(Close("Goodbye.")))

    assert [c.tag for c in root] == ["Say", "Hangup"]
    assert root[0].text == "Goodbye."


def test_urls_with_a_query_string_get_an_ampersand():
    renderer = _renderer(input_url=f"{INPUT}?x=1", wait_url=f"{WAIT}?x=1")

    listen = _parse(renderer.render(Listen("Hi", turn=2)))
    hold = _parse(renderer.render(Hold("Hi", turn=2, attempt=1)))

    assert parse_qs(urlparse(listen[0].attrib["action"]).query) == {"x": ["1"], "turn": ["2"]}
    assert "?x=1&turn=2" in listen[0].attrib["action"]
    assert "?x=1&turn=2&attempt=1" in (hold[1].text or "")


def test_reply_text_cannot_inject_twiml():
    evil = "</Say><Dial>+15550001111</Dial> a < b & \"c\" 'd'"

    for step in (Listen(evil, 2), Hold(evil, 2, 1), Close(evil)):
        root = _parse(_renderer().render(step))
        assert not list(root.iter("Dial"))
        says = list(root.iter("Say"))
        assert len(says) == 1
        assert says[0].text == evil


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("  hello \n\t  there  ", "hello there"),
        ("", FALLBACK_LINE),
        ("   \n ", FALLBACK_LINE),
    ],
    ids=["collapses_whitespace", "empty_gets_fallback", "blank_gets_fallback"],
)
def test_say_text_is_normalised(text, expected):
    root = _parse(_renderer().render(Close(text)))
    assert root[0].text == expected


def test_overlong_text_is_truncated_to_the_say_limit():
    root = _parse(_renderer().render(Close("a" * (MAX_SAY_CHARS + 500))))
    assert root[0].text == "a" * MAX_SAY_CHARS


# --- step_for -------------------------------------------------------------------------


def _result(reply: str, customer: str | None, *, usage_limited: bool = False) -> TurnResult:
    now = datetime(2026, 10, 21, 15, 0, tzinfo=UTC)
    messages = []
    if customer is not None:
        messages.append(MessageView(2, "customer", customer, now))
    messages.append(MessageView(3, "agent", reply, now))
    return TurnResult(
        interaction_id=uuid.uuid4(),
        reply_text=reply,
        intent="FAQ",
        status="in_progress",
        ended=False,
        fallback=usage_limited,
        usage_limited=usage_limited,
        messages=tuple(messages),
    )


def test_usage_limit_closes_with_the_fallback_and_goodbye():
    step = step_for(
        _result("Please call us on 555.", "what are your hours", usage_limited=True), next_turn=3
    )
    assert step == Close(f"Please call us on 555. {GOODBYE}", cacheable=True)


def test_goodbye_from_the_caller_closes_with_just_the_reply():
    assert step_for(_result("Take care!", "ok bye"), next_turn=3) == Close("Take care!")


def test_ordinary_turn_keeps_listening_on_the_next_turn():
    assert step_for(_result("We open at nine.", "when do you open"), next_turn=5) == Listen(
        "We open at nine.", 5
    )


def test_turn_without_a_customer_message_keeps_listening():
    assert step_for(_result("Hello!", None), next_turn=2) == Listen("Hello!", 2)


# --- is_goodbye -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "utterance",
    [
        "bye",
        "Goodbye!",
        "ok bye",
        "no that's all thanks",
        "that's it, thank you",
        "nothing else",
        "have a great day",
        "thanks, bye bye",
        "I'm all set",
        "that’s all",
        "no, nothing else, thanks",
    ],
    ids=[
        "bye",
        "goodbye_punct",
        "ok_bye",
        "no_thats_all",
        "thats_it",
        "nothing_else",
        "great_day",
        "bye_bye",
        "all_set",
        "curly_apostrophe",
        "no_nothing_else",
    ],
)
def test_farewells_end_the_call(utterance):
    assert is_goodbye(utterance) is True


@pytest.mark.parametrize(
    "utterance",
    [
        "no thanks",
        "no",
        "yes please",
        "can I come by tomorrow",
        "before I say goodbye what time do you close",
        "goodbye is that all you charge",
        "don't hang up",
        "wait",
        "hold on, not yet, bye",
        "I would like a haircut tomorrow afternoon and then maybe a colour and goodbye",
        "",
        "that's all I wanted to ask, what time do you close",
        "is that all you have",
        "what time do you close, bye",
        "that's all I have on Tuesday",
        "I'm good with the 3:30",
    ],
    ids=[
        "no_thanks_declines_offer",
        "bare_no",
        "yes_please",
        "question_can_i",
        "before_goodbye_question",
        "goodbye_then_question",
        "dont_hang_up",
        "wait",
        "hold_on_not_yet",
        "long_sentence",
        "empty",
        "thats_all_then_question",
        "is_that_all_question",
        "question_then_bye",
        "thats_all_then_more",
        "im_good_with_a_time",
    ],
)
def test_other_utterances_keep_the_call_going(utterance):
    assert is_goodbye(utterance) is False


# --- <Play> (slice 15) ---------------------------------------------------------------

MEDIA = "https://api.example.test/api/v1/media"
TOKEN = "A" * 21 + "_-" + "b" * 20  # the 43-character shape `MediaStore` mints


def _voiced_renderer() -> TwimlRenderer:
    return TwimlRenderer(VoiceUrls(input=INPUT, wait=WAIT, media=MEDIA))


def test_a_step_with_audio_plays_the_media_url_instead_of_saying_it():
    listen = _parse(_voiced_renderer().render(Listen("How can I help?", 2, audio=TOKEN)))
    hold = _parse(_voiced_renderer().render(Hold("One moment, please.", 2, 1, audio=TOKEN)))
    close = _parse(_voiced_renderer().render(Close("Goodbye.", audio=TOKEN)))

    gather = listen.find("Gather")
    assert gather is not None
    assert [child.tag for child in gather] == ["Play"]
    assert gather[0].text == f"{MEDIA}/{TOKEN}"
    assert [child.tag for child in hold] == ["Play", "Redirect"]
    assert [child.tag for child in close] == ["Play", "Hangup"]
    assert "Goodbye." not in _voiced_renderer().render(Close("Goodbye.", audio=TOKEN))


def test_a_step_without_audio_or_with_a_malformed_token_says_the_line():
    for audio in (None, "", "short", TOKEN + "x", "../../etc/passwd" + "a" * 27):
        root = _parse(_voiced_renderer().render(Close("Goodbye.", audio=audio)))
        assert [child.tag for child in root] == ["Say", "Hangup"], audio
        assert root[0].text == "Goodbye."


def test_steps_are_not_cacheable_unless_marked():
    reply = step_for(_result("Sure, 2 PM works.", "book me at 2"), next_turn=2)
    goodbye = step_for(_result("Bye!", "goodbye"), next_turn=2)

    assert not reply.cacheable and not goodbye.cacheable
    assert not Listen("x", 1).cacheable and not Hold("x", 1, 1).cacheable
    assert not Close("x").cacheable
