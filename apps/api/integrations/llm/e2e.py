"""A deterministic, rule-based stand-in for Gemini, for the Playwright E2E run only.

`E2EScriptedLLM` implements the `LLMClient` port with plain text rules instead of a model,
so a browser journey is repeatable and costs no provider request. It is NOT a test double
for unit tests (`FakeLLMClient` scripts replies turn by turn); it answers whatever request
arrives, the way the engine builds it (`services/prompt_builder.py`), and it is wired in
only by `E2E_FAKE_PROVIDERS=1` (`core/llm.py`, refused in production). It keeps no state
between calls: everything it needs is in the request (the conversation, and the TOOL
RESULTS / FAQ RESULTS the backend verified in this turn).

What it does, by the customer's last message (every reply is a valid `AgentTurn` that
passes the backend's grounding, time, booking and callback guards):

- "manager", "speak to a person", ... -> HUMAN_REQUEST; "complaint", "refund", ... ->
  COMPLAINT. `requires_human` is set, the reply says the team will be notified (the
  escalation policy records it). 1 model request.
- "book", "appointment", ... (or a reply to one of its own questions that starts with
  "To book") -> `get_services`, `check_availability`, then `create_booking` once the
  service, a date, an offered time and the customer's name are known, then a confirmation
  built from the confirmed booking ("You're booked: <service> on <weekday>, <Month> <day>
  at <h:mm AM/PM>, under the name <name>."). Missing details are asked for one at a time,
  each ask starting with "To book". 2 to 4 requests per turn.
- anything else -> `search_faq` with the question, then the best-matching FAQ answer
  verbatim, citing its id; "Sorry, I don't have that information." when nothing matches.
  2 requests (1 for "hi" / "thanks").
- an interaction summary request -> two sentences that follow the verified outcome.

Details it reads from the customer: a service name (any listed service named in the text;
the only service when there is one), a date (`YYYY-MM-DD`, "today", "tomorrow", a weekday
name), a time ("10:00", "2:30 pm", "10am") and a name ("my name is Sam Tester", "I'm ...",
"under ...", or a short reply to its name question).

This module must stay free of `services` imports (layering): it only speaks the port.
"""

import json
import re
from datetime import date, timedelta
from typing import Any

from integrations.llm.port import ModelMessage, ModelReply, ModelRequest

TOKENS_IN = 100
TOKENS_OUT = 20

ASK_PREFIX = "To book"
NO_INFO = "Sorry, I don't have that information."
GREETING_REPLY = "Hello! How can I help you today?"
HUMAN_REPLY = (
    "Of course, I understand you'd like to speak with a person. "
    "The team will be notified and will review your request."
)
COMPLAINT_REPLY = (
    "I'm sorry to hear that, and I understand your frustration. "
    "The team will be notified and will review your complaint."
)

_HUMAN = re.compile(
    r"\b(manager|supervisor|owner|representative|real person|human|"
    r"(speak|talk|chat)\s+(to|with)\s+(a\s+|an\s+|the\s+)?(person|someone|somebody|staff))\b",
    re.IGNORECASE,
)
_COMPLAINT = re.compile(
    r"\b(complain\w*|refund|unhappy|terrible|awful|ruined|unacceptable|disappointed|"
    r"not\s+happy)\b",
    re.IGNORECASE,
)
_BOOKING = re.compile(r"\b(book\w*|appointment|reserve|schedule)\b", re.IGNORECASE)
_GREETING = re.compile(
    r"^\s*(hi|hello|hey|thanks|thank you|good (morning|afternoon|evening))\W*$", re.IGNORECASE
)
# Endings of its own questions: a reply to one of them continues the booking.
_BOOKING_ASKS = (
    "Which time would you like?",
    "Which would you like?",
    "another day",
    "may I have your name?",
)
_TODAY = re.compile(r"Today: (\w+) (\d{4}-\d{2}-\d{2})")
_TOOL_LINE = re.compile(r"^\d+\. (\S+): (\w+) (\{.*\})\s*$", re.MULTILINE)
_FAQ_ENTRY = re.compile(
    r"FAQ id: (\S+)\n(?:Category: [^\n]*\n)?Question: ([^\n]*)\nAnswer:(.*?)(?=\n\nFAQ id: |\Z)",
    re.DOTALL,
)
_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b", re.IGNORECASE)
_HOUR_AMPM = re.compile(r"\b(\d{1,2})\s*(am|pm)\b", re.IGNORECASE)
_NAME = re.compile(
    r"(?:my name is|my name's|name is|name:|i am|i'm|this is|under(?: the name)?|call me)\s+"
    r"([A-Za-z][A-Za-z'-]*(?:\s+[A-Za-z][A-Za-z'-]*){0,2})",
    re.IGNORECASE,
)
# Words that end a name ("my name is Sam Tester and ...") or show it was not a name.
_NAME_STOP = frozenset(
    "and at on for to the a an with please book booking tomorrow today looking would like "
    "hoping here calling in is wanting want need".split()
)
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_STOP_WORDS = frozenset(
    "the a an is are do you your i we can what how when where who why does of to for and or "
    "in on at my me it be have has with about any there this that".split()
)
_WANTED = {
    "RESCHEDULE": "The customer asked to reschedule an appointment.",
    "CANCEL": "The customer asked to cancel an appointment.",
    "PRICE_INQUIRY": "The customer asked about prices.",
    "BUSINESS_HOURS": "The customer asked about opening hours.",
    "LOCATION": "The customer asked about the business location.",
    "FAQ": "The customer asked a general question.",
    "HUMAN_REQUEST": "The customer asked to speak with a person.",
    "COMPLAINT": "The customer raised a complaint.",
    "URGENT": "The customer reported an urgent matter.",
}


class E2EScriptedLLM:
    """See the module docstring. One `generate` is one deterministic reply."""

    async def generate(self, request: ModelRequest) -> ModelReply:
        if request.response_schema.__name__ == "SummaryReply":
            payload: dict[str, Any] = {"summary": _summary(request.system_instruction)}
        else:
            payload = _Turn(request).reply()
        return ModelReply(text=json.dumps(payload), tokens_in=TOKENS_IN, tokens_out=TOKENS_OUT)


def _turn(
    text: str,
    *,
    intent: str = "UNKNOWN",
    sources: list[str] | None = None,
    entities: dict[str, str] | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    requires_human: bool = False,
) -> dict[str, Any]:
    return {
        "intent": intent,
        "response_text": text,
        "entities": entities or {},
        "requires_human": requires_human,
        "tool_calls": tool_calls or [],
        "sources": sources or [],
    }


def _call(name: str, arguments: dict[str, Any], intent: str) -> dict[str, Any]:
    return _turn("One moment.", intent=intent, tool_calls=[{"name": name, "arguments": arguments}])


def _clock12(hhmm: str) -> str:
    hour, minute = (int(part) for part in hhmm.split(":"))
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def _day_label(iso_day: str) -> str:
    day = date.fromisoformat(iso_day)
    return f"{day.strftime('%A')}, {day.strftime('%B')} {day.day}"


def _join(items: list[str]) -> str:
    return ", ".join(items[:-1]) + (" or " if len(items) > 1 else "") + items[-1]


class _Result:
    def __init__(self, name: str, status: str, data: dict[str, Any]) -> None:
        self.name, self.status, self.data = name, status, data

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


class _Turn:
    def __init__(self, request: ModelRequest) -> None:
        self.system = request.system_instruction
        self.tools = {tool.name for tool in request.tools}
        self.results = self._tool_results()
        messages = list(request.messages)
        self.utterance = messages[-1].text if messages else ""
        self.user_texts = self._booking_thread(messages)
        today = _TODAY.search(self.system)
        self.today = date.fromisoformat(today.group(2)) if today else date.today()
        self.previous_agent = next(
            (m.text for m in reversed(messages[:-1]) if m.role == "model"), ""
        )

    # --- reading the request -------------------------------------------------------

    def _tool_results(self) -> list[_Result]:
        start = self.system.find("<<<TOOL_RESULTS>>>")
        end = self.system.find("<<<END_TOOL_RESULTS>>>")
        if start < 0 or end < 0:
            return []
        found: list[_Result] = []
        for match in _TOOL_LINE.finditer(self.system[start:end]):
            try:
                data = json.loads(match.group(3))
            except ValueError:
                data = {}
            found.append(_Result(match.group(1), match.group(2), data))
        return found

    def _last(self, name: str) -> _Result | None:
        return next((r for r in reversed(self.results) if r.name == name), None)

    @staticmethod
    def _booking_thread(messages: list[ModelMessage]) -> list[str]:
        """Customer messages since the last confirmed booking, newest first."""
        thread: list[str] = []
        for message in reversed(messages):
            if message.role == "model" and "You're booked" in message.text:
                break
            if message.role == "user":
                thread.append(message.text)
        return thread

    def _faqs(self) -> list[tuple[str, str, str]]:
        start = self.system.find("<<<FAQ_RESULTS>>>")
        end = self.system.find("<<<END_FAQ_RESULTS>>>")
        if start < 0 or end < 0:
            return []
        faqs = []
        for match in _FAQ_ENTRY.finditer(self.system[start + len("<<<FAQ_RESULTS>>>") : end]):
            lines = [ln[1:].strip() for ln in match.group(3).split("\n") if ln.startswith("|")]
            answer = " ".join(ln for ln in lines if ln)
            if answer:
                faqs.append((match.group(1), match.group(2), answer))
        return faqs

    # --- routing ---------------------------------------------------------------------

    def reply(self) -> dict[str, Any]:
        text = self.utterance
        if _HUMAN.search(text):
            return _turn(HUMAN_REPLY, intent="HUMAN_REQUEST", requires_human=True)
        if _COMPLAINT.search(text):
            return _turn(COMPLAINT_REPLY, intent="COMPLAINT", requires_human=True)
        booking_tools = {"get_services", "check_availability", "create_booking"} <= self.tools
        asked = self.previous_agent.startswith(ASK_PREFIX) or any(
            marker in self.previous_agent for marker in _BOOKING_ASKS
        )
        if booking_tools and (_BOOKING.search(text) or asked or self._last("get_services")):
            return self._booking()
        if _GREETING.match(text):
            return _turn(GREETING_REPLY)
        return self._faq()

    # --- FAQ -------------------------------------------------------------------------

    def _faq(self) -> dict[str, Any]:
        searched = self._last("search_faq")
        if searched is None and "search_faq" in self.tools:
            return _call("search_faq", {"query": self.utterance.strip()[:200] or "help"}, "FAQ")
        wanted = _words(self.utterance)
        best: tuple[int, tuple[str, str, str]] | None = None
        for faq in self._faqs():
            score = len(wanted & _words(f"{faq[1]} {faq[2]}"))
            if score > 0 and (best is None or score > best[0]):
                best = (score, faq)
        if best is None:
            return _turn(NO_INFO)
        faq_id, _, answer = best[1]
        return _turn(answer[:1000], intent="FAQ", sources=[faq_id])

    # --- booking -----------------------------------------------------------------------

    def _booking(self) -> dict[str, Any]:
        intent = "BOOK_APPOINTMENT"
        listing = self._last("get_services")
        if listing is None:
            return _call("get_services", {}, intent)
        services = listing.data.get("services") or []
        if not listing.ok or not services:
            return _turn("Sorry, I can't see any services to book right now.", intent=intent)

        service = self._service(services)
        if service is None:
            names = _join([s["name"] for s in services])
            return _turn(
                f"{ASK_PREFIX}, which service would you like: {names}?",
                intent=intent,
                sources=[s["id"] for s in services],
            )
        sources = [service["id"]]
        entities = {"service_name": service["name"][:80]}
        when = self._date()
        if when is None:
            return _turn(
                f"{ASK_PREFIX} {service['name']}, which day works for you?",
                intent=intent,
                sources=sources,
                entities=entities,
            )
        clock = self._time()
        name = self._name()
        entities |= {"requested_date": when, **({"requested_time": clock} if clock else {})}
        if name:
            entities["customer_name"] = name[:80]

        checked = self._last("check_availability")
        if checked is None:
            args: dict[str, Any] = {"service": service["id"], "appointment_date": when}
            if clock:
                args["around"] = clock
            return _call("check_availability", args, intent)
        if not checked.ok or checked.data.get("result") not in ("available", "no_availability"):
            return _turn(
                "Sorry, I couldn't check that day. Which other day would you like?",
                intent=intent,
                sources=sources,
                entities=entities,
            )
        if checked.data["result"] == "no_availability":
            return _turn(
                "Sorry, nothing is open on that day. Would you like to try another day?",
                intent=intent,
                sources=sources,
                entities=entities,
            )
        offered: list[str] = list(checked.data.get("offered_times") or [])
        day = _day_label(checked.data["date"])
        if not offered:
            return _turn(
                "Sorry, nothing is open on that day. Would you like to try another day?",
                intent=intent,
                sources=sources,
                entities=entities,
            )
        if clock is None or clock not in offered:
            times = _join([_clock12(t) for t in offered])
            lead = "Sorry, that time isn't available. " if clock else ""
            return _turn(
                f"{lead}On {day} I have {times}. Which time would you like?",
                intent=intent,
                sources=sources,
                entities=entities,
            )
        if not name:
            return _turn(
                f"{ASK_PREFIX} {service['name']} on {day} at {_clock12(clock)}, "
                "may I have your name?",
                intent=intent,
                sources=sources,
                entities=entities,
            )

        booked = self._last("create_booking")
        if booked is None:
            return _call(
                "create_booking",
                {
                    "service": service["id"],
                    "appointment_date": when,
                    "start_time": clock,
                    "customer_name": name,
                },
                intent,
            )
        return self._booked(booked, sources, entities)

    def _booked(
        self, booked: _Result, sources: list[str], entities: dict[str, str]
    ) -> dict[str, Any]:
        intent = "BOOK_APPOINTMENT"
        result = booked.data.get("result") if booked.ok else None
        if result == "confirmed":
            info = booked.data["booking"]
            text = (
                f"You're booked: {info['service']} on {_day_label(info['date'])} at "
                f"{_clock12(info['start_time'])}, under the name {info['customer_name']}."
            )
            return _turn(text, intent=intent, sources=sources, entities=entities)
        alternatives: list[str] = list(booked.data.get("offered_times") or [])
        if result == "slot_taken" and alternatives:
            times = _join([_clock12(t) for t in alternatives])
            return _turn(
                f"Sorry, that time isn't available. I have {times}. Which would you like?",
                intent=intent,
                sources=sources,
                entities=entities,
            )
        if result in ("invalid", "slot_taken"):
            return _turn(
                "Sorry, I can't book that one, so nothing is booked yet. "
                "Could you pick another day or time?",
                intent=intent,
                sources=sources,
                entities=entities,
            )
        return _turn(
            "I'm sorry, I couldn't complete that booking, so nothing is booked yet.",
            intent=intent,
            sources=sources,
            entities=entities,
            requires_human=True,
        )

    # --- details from the customer's messages ----------------------------------------

    def _service(self, services: list[dict[str, Any]]) -> dict[str, Any] | None:
        for text in self.user_texts:
            lowered = text.lower()
            for service in services:
                if str(service["name"]).lower() in lowered:
                    return service
        return services[0] if len(services) == 1 else None

    def _date(self) -> str | None:
        for text in self.user_texts:
            if match := _ISO_DATE.search(text):
                return match.group(1)
            lowered = text.lower()
            if "tomorrow" in lowered:
                return (self.today + timedelta(days=1)).isoformat()
            if re.search(r"\btoday\b", lowered):
                return self.today.isoformat()
            for number, weekday in enumerate(_WEEKDAYS):
                if re.search(rf"\b{weekday}\b", lowered):
                    ahead = (number - self.today.weekday()) % 7 or 7
                    return (self.today + timedelta(days=ahead)).isoformat()
        return None

    def _time(self) -> str | None:
        for text in self.user_texts:
            text = _ISO_DATE.sub(" ", text)
            if match := _CLOCK.search(text):
                hour, minute, meridiem = int(match.group(1)), int(match.group(2)), match.group(3)
            elif match := _HOUR_AMPM.search(text):
                hour, minute, meridiem = int(match.group(1)), 0, match.group(2)
            else:
                continue
            if meridiem:
                hour = hour % 12 + (12 if meridiem.lower() == "pm" else 0)
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return f"{hour:02d}:{minute:02d}"
        return None

    def _name(self) -> str | None:
        for index, text in enumerate(self.user_texts):
            if match := _NAME.search(text):
                words: list[str] = []
                for word in match.group(1).split():
                    if word.lower() in _NAME_STOP:
                        break
                    words.append(word)
                if words:
                    return " ".join(words)
            # A short reply to "may I have your name?" is the name itself.
            if index == 0 and self.previous_agent.endswith("may I have your name?"):
                cleaned = re.sub(r"[^A-Za-z' -]", "", text).strip()
                if cleaned and len(cleaned.split()) <= 3 and not _CLOCK.search(text):
                    return cleaned
        return None


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if len(w) > 2} - _STOP_WORDS


# --- Interaction summaries ---------------------------------------------------------


def _summary(instruction: str) -> str:
    """Two sentences from the VERIFIED_OUTCOME block (what the backend computed)."""
    outcome = _field(instruction, r"- outcome: (\w+)") or "answered"
    intent = _field(instruction, r"- the customer's main request: (\w+)") or "UNKNOWN"
    booked = _field(instruction, r"- appointment booked: yes, (.+)")
    failed = "FAILED" in (_field(instruction, r"- appointment booked: (no.*)") or "")

    service = label = None
    if booked:
        service, _, label = booked.rpartition(" on ")
        service = re.sub(r"[.!?<>]+", " ", service).strip()
    if intent == "BOOK_APPOINTMENT" or (booked and intent == "UNKNOWN"):
        first = (
            f"The customer asked to book a {service}."
            if service
            else ("The customer asked to book an appointment.")
        )
    else:
        first = _WANTED.get(intent, "The customer contacted the business.")
    if outcome == "booked":
        second = (
            f"The appointment was booked for {label}." if label else ("The appointment was booked.")
        )
    elif outcome == "failed":
        second = (
            "The booking could not be completed and nothing was booked."
            if failed
            else "The assistant could not complete the request."
        )
    elif outcome == "escalated":
        second = "The request was flagged for the business to follow up."
    elif outcome == "abandoned":
        second = "The customer left before the request was completed."
    elif intent in ("BOOK_APPOINTMENT", "RESCHEDULE") or failed:
        second = "No appointment was booked."
    else:
        second = "The assistant answered the question."
    return f"{first} {second}"


def _field(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else None
