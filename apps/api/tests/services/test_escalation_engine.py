"""The escalation policy wired into the conversation engine: scripted fake model,
in-memory stores and a recording notifier. No DB, no provider."""

import pytest

from core.retry import RetryPolicy
from integrations.llm.fake import FakeLLMClient, agent_turn
from services.conversation import ConversationEngine
from services.escalation import EscalationService, EscalationSettings
from services.escalation_policy import EMERGENCY_REPLY
from services.tools import ToolRegistry
from tests.services.memory_conversation_store import MemoryStore
from tests.services.memory_escalation_store import MemoryEscalationStore
from tests.services.memory_usage_store import guard
from tests.services.test_escalation import PHONE, RecordingNotifier, _tenant


class Harness:
    def __init__(self) -> None:
        self.tenant = _tenant()
        self.store = MemoryStore()
        self.escalation_store = MemoryEscalationStore()
        self.notifier = RecordingNotifier()
        self.service = EscalationService(
            self.escalation_store, self.notifier, inbox_url=lambda i: f"https://app.test/{i}"
        )
        self.store.escalation_rows = lambda: self.escalation_store.rows.values()

    def engine(self, llm: FakeLLMClient) -> ConversationEngine:
        return ConversationEngine(
            self.store,
            llm,
            ToolRegistry(),
            20,
            5,
            usage=guard(),
            llm_retry=RetryPolicy(attempts=2),
            escalations=self.service,
        )

    async def turn(self, llm, text, interaction_id=None):
        return await self.engine(llm).handle_turn(self.tenant, interaction_id, text, "test_chat")

    @property
    def rows(self):
        return list(self.escalation_store.rows.values())

    @property
    def state(self):
        return next(iter(self.store.interactions.values())).state


@pytest.fixture
def h() -> Harness:
    return Harness()


async def _sms_on(h: Harness) -> None:
    await h.service.save_settings(
        h.tenant, EscalationSettings(contact_phone=PHONE, sms_enabled=True)
    )


async def test_a_human_request_records_one_escalation_flags_and_alerts_once(h, llm):
    await _sms_on(h)
    llm.queue(agent_turn("I'll pass that on.", intent="HUMAN_REQUEST", requires_human=True))

    result = await h.turn(llm, "Can I talk to a real person?")

    assert result.escalated is True and result.reply_text == "I'll pass that on."
    assert [(r.reason, r.status) for r in h.rows] == [("human_requested", "open")]
    assert h.state["requires_human"] is True
    assert len(h.notifier.alerts) == 1
    assert h.rows[0].sms_status == "queued"


async def test_repeated_turns_keep_one_record_and_one_alert(h, llm):
    await _sms_on(h)
    llm.queue(
        agent_turn("Passing it on.", intent="HUMAN_REQUEST"),
        agent_turn("Still passing it on.", intent="HUMAN_REQUEST"),
        agent_turn("Yes, they'll call.", intent="HUMAN_REQUEST"),
    )

    first = await h.turn(llm, "I want a person")
    await h.turn(llm, "a person please", first.interaction_id)
    await h.turn(llm, "hello? a human", first.interaction_id)

    assert len(h.rows) == 1 and len(h.notifier.alerts) == 1


async def test_a_second_reason_is_recorded_but_not_alerted(h, llm):
    await _sms_on(h)
    llm.queue(
        agent_turn("On it.", intent="HUMAN_REQUEST"),
        agent_turn("Sorry about that.", intent="COMPLAINT"),
    )

    first = await h.turn(llm, "a person please")
    await h.turn(llm, "this is unacceptable", first.interaction_id)

    assert sorted(r.sms_status for r in h.rows) == ["queued", "skipped"]
    assert len(h.notifier.alerts) == 1


async def test_sms_off_records_without_sending(h, llm):
    llm.queue(agent_turn("On it.", intent="HUMAN_REQUEST"))

    await h.turn(llm, "a person please")

    assert [r.sms_status for r in h.rows] == ["disabled"] and h.notifier.alerts == []


async def test_a_failing_notifier_never_changes_the_reply(h, llm):
    from services.notifier import NotifierError

    await _sms_on(h)
    h.notifier.error = NotifierError("limit_reached")
    llm.queue(agent_turn("I'll pass that on.", intent="HUMAN_REQUEST"))

    result = await h.turn(llm, "a person please")

    assert result.reply_text == "I'll pass that on." and result.escalated is True
    assert (h.rows[0].sms_status, h.rows[0].sms_error) == ("failed", "limit_reached")


async def test_an_urgent_turn_without_an_emergency_pointer_gets_the_fixed_reply(h, llm):
    llm.queue(agent_turn("Sure, what time works for you?", intent="BOOK_APPOINTMENT"))

    result = await h.turn(llm, "My husband is having a heart attack, he can't breathe")

    assert result.reply_text == EMERGENCY_REPLY
    assert result.messages[-1].text == EMERGENCY_REPLY
    assert [(r.reason, r.urgency) for r in h.rows] == [("urgent", "high")]
    stored = [m.message_text for m in h.store.messages if m.speaker == "agent"]
    assert stored[-1] == EMERGENCY_REPLY


async def test_an_urgent_turn_that_already_points_to_emergency_services_keeps_its_reply(h, llm):
    reply = "Please call 911 right away. I've let the team know."
    llm.queue(agent_turn(reply, intent="URGENT", requires_human=True))

    result = await h.turn(llm, "someone collapsed and isn't breathing")

    assert result.reply_text == reply
    assert [r.reason for r in h.rows] == ["urgent"]


async def test_an_owner_keyword_is_urgent(h, llm):
    await h.service.save_settings(h.tenant, EscalationSettings(urgent_keywords=("gas leak",)))
    llm.queue(agent_turn("Noted.", intent="FAQ"))

    await h.turn(llm, "There is a gas leak in my unit")

    assert [r.reason for r in h.rows] == ["urgent"]


@pytest.mark.parametrize(
    "text",
    ["What are your hours?", "Do you do balayage?", "I'd like a haircut on Friday"],
    ids=["hours", "service", "booking"],
)
async def test_ordinary_turns_do_not_escalate(h, llm, text):
    await _sms_on(h)
    llm.queue(agent_turn("Happy to help.", intent="UNKNOWN"))

    result = await h.turn(llm, text)

    assert result.escalated is False and h.rows == [] and h.notifier.alerts == []
    assert "requires_human" not in h.state or h.state["requires_human"] is False


async def test_a_true_team_notified_reply_is_kept_once_an_earlier_turn_escalated(h, llm):
    llm.queue(
        agent_turn("I'll pass that on.", intent="HUMAN_REQUEST"),
        agent_turn("Yes, I've let the team know.", intent="UNKNOWN"),
    )

    first = await h.turn(llm, "a person please")
    second = await h.turn(llm, "did you tell them?", first.interaction_id)

    assert second.reply_text == "Yes, I've let the team know." and second.fallback is False
    assert [r.reason for r in h.rows] == ["human_requested"]
    assert not any(f["kind"] == "unconfirmed_handoff" for f in h.state.get("failures", []))


async def test_repeated_unanswerable_turns_exhaust_clarification(h, llm):
    # Invalid output twice per turn: each turn is one clarification attempt (default 2).
    llm.queue("nope", "nope", "nope", "nope")

    first = await h.turn(llm, "blah")
    assert h.rows == []
    await h.turn(llm, "blah blah", first.interaction_id)

    assert [r.reason for r in h.rows] == ["clarification_exhausted"]
    assert h.state["clarification_attempts"] == 2
