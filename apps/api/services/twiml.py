"""`TwimlRenderer`: what Twilio does next on a call, as TwiML. Pure; no I/O.

A call is a loop of `CallStep`s decided by `services/call_session.py`:

- `Listen(text, turn)`: say `text`, then gather the caller's speech and post it to the
  input webhook as turn `turn` (silence posts too, with no `SpeechResult`, so the
  backend decides the reprompt; `silences` rides in the URL Twilio signs).
- `Hold(text, turn, attempt)`: say a short holding phrase and redirect to the wait
  webhook, which returns the turn's reply once a worker has stored it.
- `Close(text)`: say `text` and hang up.

`step_for(result, ...)` maps an engine `TurnResult` to the next step: a reached usage
limit (the quota fallback, which already points to the business's own contacts) and a
caller saying goodbye end the call; anything else keeps listening. Swapping `<Say>` for
`<Play>` (Gemini TTS, next slice) changes only this module.

Every text goes through an XML builder, never string formatting, so nothing a caller or
the model says can inject TwiML.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlencode
from xml.etree import ElementTree

from services.conversation import TurnResult

# Twilio's <Say> limit is 4096 characters; replies are far shorter, this is a backstop.
MAX_SAY_CHARS = 4000
LANGUAGE = "en-US"

GOODBYE = "Thanks for calling. Goodbye."


@dataclass(frozen=True)
class Listen:
    text: str
    turn: int
    silences: int = 0


@dataclass(frozen=True)
class Hold:
    text: str
    turn: int
    attempt: int


@dataclass(frozen=True)
class Close:
    text: str


CallStep = Listen | Hold | Close


@dataclass(frozen=True)
class VoiceUrls:
    """Absolute webhook URLs (the public origin Twilio signs) or paths."""

    input: str
    wait: str


class TwimlRenderer:
    def __init__(self, urls: VoiceUrls) -> None:
        self._urls = urls

    def render(self, step: CallStep) -> str:
        response = ElementTree.Element("Response")
        match step:
            case Listen(text=text, turn=turn, silences=silences):
                query = {"turn": str(turn)}
                if silences:
                    query["silences"] = str(silences)
                gather = ElementTree.SubElement(
                    response,
                    "Gather",
                    {
                        "input": "speech",
                        "action": _with_query(self._urls.input, query),
                        "method": "POST",
                        "language": LANGUAGE,
                        "speechTimeout": "auto",
                        "speechModel": "phone_call",
                        # Silence still posts (no SpeechResult): the backend reprompts.
                        "actionOnEmptyResult": "true",
                    },
                )
                _say(gather, text)
            case Hold(text=text, turn=turn, attempt=attempt):
                _say(response, text)
                redirect = ElementTree.SubElement(response, "Redirect", {"method": "POST"})
                redirect.text = _with_query(
                    self._urls.wait, {"turn": str(turn), "attempt": str(attempt)}
                )
            case Close(text=text):
                _say(response, text)
                ElementTree.SubElement(response, "Hangup")
        body = ElementTree.tostring(response, encoding="unicode")
        return f'<?xml version="1.0" encoding="UTF-8"?>{body}'


def step_for(result: TurnResult, *, next_turn: int) -> CallStep:
    """The step after an engine turn. A usage limit hangs up after the quota fallback
    (which already gives the business's contacts); a caller's goodbye ends the call after
    the agent's reply; otherwise keep listening."""
    if result.usage_limited:
        return Close(f"{result.reply_text} {GOODBYE}")
    customer = next((m.text for m in result.messages if m.speaker == "customer"), "")
    if is_goodbye(customer):
        return Close(result.reply_text)
    return Listen(result.reply_text, next_turn)


def _say(parent: ElementTree.Element, text: str) -> None:
    say = ElementTree.SubElement(parent, "Say", {"language": LANGUAGE})
    say.text = _speakable(text)


def _speakable(text: str) -> str:
    clean = " ".join(text.split())
    return clean[:MAX_SAY_CHARS] if clean else "Sorry, could you say that again?"


def _with_query(url: str, query: dict[str, str]) -> str:
    return f"{url}{'&' if '?' in url else '?'}{urlencode(query)}"


# --- goodbye detection --------------------------------------------------------------
# Conservative on purpose: hanging up on a caller who wasn't done is worse than one more
# "anything else?". Only a SHORT utterance that is a farewell counts, never a question,
# and never a bare "no thanks" (that usually declines an offer, not the call).
MAX_GOODBYE_WORDS = 8
_FAREWELL = re.compile(
    r"\b(?:good ?bye|bye(?: bye)?|bye now|see you|see ya|talk to you later|"
    r"have a (?:good|great|nice) (?:day|one|night|evening|weekend))\b"
)
_DONE = re.compile(
    r"^(?:(?:no|nope|nah|ok|okay|great|perfect|cool|alright|all right)\b[\s,]*)*"
    r"(?:that(?:'s| is| was) (?:all|it|everything)|nothing else|i'm all set|i am all set|"
    r"i'm good|i am good)"
    # The phrase must END the utterance (only thanks or a farewell after it), so "that's
    # all I have on Tuesday" is not a goodbye.
    # Each alternative starts with a different word, so the repetition is unambiguous (no
    # catastrophic backtracking); "bye bye" is simply "bye" twice.
    r"(?: (?:for now|thanks|thank you|so much|very much|bye|goodbye|good bye))*$"
)
_QUESTION = re.compile(
    r"\?|\b(?:what|when|where|which|how|why|who|can|could|would|do|does|is|are|will)\b"
)
_NEGATED = re.compile(r"\b(?:don't|do not|not|before|wait)\b")


def is_goodbye(utterance: str) -> bool:
    text = utterance.lower().replace("’", "'")
    words = re.findall(r"[a-z']+", text)
    if not words or len(words) > MAX_GOODBYE_WORDS:
        return False
    normalized = " ".join(words)
    if "?" in text or _QUESTION.search(normalized) or _NEGATED.search(normalized):
        return False
    return bool(_FAREWELL.search(normalized) or _DONE.search(normalized))
