"""Review follow-ups for slice 13: handoff claims, one alert per interaction, commit
failures, reopening, consecutive clarification attempts, owner-keyword urgency."""

import uuid

import pytest

from integrations.llm.fake import FakeLLMClient, agent_turn
from services.conversation import handoff_fallback
from services.escalation import EscalationSettings
from services.escalation_policy import EMERGENCY_REPLY, EscalationDecision
from tests.services.test_escalation import PHONE, _decision
from tests.services.test_escalation import notifier as notifier  # noqa: F401  (fixtures)
from tests.services.test_escalation import store as store  # noqa: F401
from tests.services.test_escalation import tenant as tenant  # noqa: F401
from tests.services.test_escalation_engine import Harness, _sms_on


@pytest.fixture
def h() -> Harness:
    return Harness()


# --- 1. a claim that the team was told, with no escalation behind it ----------------------


async def test_a_team_notified_claim_without_an_escalation_gets_the_handoff_fallback(h, llm):
    await _sms_on(h)
    llm.queue(agent_turn("I've let the team know.", intent="UNKNOWN"))

    result = await h.turn(llm, "Thanks, that is all")

    business = h.tenant.business
    assert business is not None
    assert result.reply_text == handoff_fallback(business)
    assert "let the team know" not in result.reply_text
    assert result.escalated is False and h.rows == [] and h.notifier.alerts == []
    assert h.state["requires_human"] is True
    assert [f["kind"] for f in h.state["failures"]] == ["unconfirmed_handoff"]
    assert h.state["fallback_messages"] == [result.messages[-1].sequence_number]
    stored = [m.message_text for m in h.store.messages if m.speaker == "agent"]
    assert stored[-1] == result.reply_text


async def test_the_same_claim_is_kept_when_an_escalation_exists(h, llm):
    llm.queue(agent_turn("I've let the team know.", intent="HUMAN_REQUEST"))

    result = await h.turn(llm, "I want a person")

    assert result.reply_text == "I've let the team know." and len(h.rows) == 1


# --- 3. a failed turn commit is not swallowed --------------------------------------------


async def test_a_failing_turn_commit_propagates(h, llm):
    await _sms_on(h)
    llm.queue(agent_turn("Passing it on.", intent="HUMAN_REQUEST"))
    original = h.store.checkpoint
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        # The checkpoint before the model call succeeds; the one that commits the turn fails.
        if calls["n"] == 2:
            raise RuntimeError("commit failed")
        await original()

    h.store.checkpoint = flaky  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="commit failed"):
        await h.turn(llm, "a person please")
    assert h.notifier.alerts == []


# --- 4. reopening -------------------------------------------------------------------------


async def test_a_resolved_turn_reason_reopens_and_alerts_again(h, llm):
    await _sms_on(h)
    llm.queue(
        agent_turn("On it.", intent="HUMAN_REQUEST"),
        agent_turn("Noted again.", intent="HUMAN_REQUEST"),
    )
    first = await h.turn(llm, "a person please")
    await h.service.resolve(h.tenant, h.rows[0].id)
    assert h.rows[0].status == "resolved"

    second = await h.turn(llm, "hello? a person please", first.interaction_id)

    assert len(h.rows) == 1
    assert h.rows[0].status == "open" and h.rows[0].resolved_at is None
    assert second.escalated is True
    assert len(h.notifier.alerts) == 2


async def test_a_resolved_history_reason_stays_resolved_and_flags_nothing(h, llm):
    interaction = uuid.uuid4()
    decision = EscalationDecision("integration_failing", "normal", ())
    recorded = await h.service.record(h.tenant, interaction, decision)
    await h.service.resolve(h.tenant, recorded.record.id)

    again = await h.service.record(h.tenant, interaction, decision)

    assert again.created is False and again.reopened is False
    assert again.record.status == "resolved"


async def test_reopening_a_turn_reason_through_the_service(h):
    await h.service.save_settings(
        h.tenant, EscalationSettings(contact_phone=PHONE, sms_enabled=True)
    )
    interaction = uuid.uuid4()
    first = await h.service.record(h.tenant, interaction, _decision("complaint"))
    await h.service.resolve(h.tenant, first.record.id)

    again = await h.service.record(h.tenant, interaction, _decision("complaint"))

    assert again.reopened is True and again.record.status == "open"
    assert again.record.sms_status == "pending"


# --- 5. clarification attempts are consecutive --------------------------------------------


async def test_a_good_reply_resets_the_clarification_streak(h, llm):
    # fail, success, fail -> streak of 1 (threshold 2): no escalation.
    llm.queue("nope", "nope", agent_turn("Happy to help.", intent="UNKNOWN"), "nope", "nope")

    first = await h.turn(llm, "blah")
    await h.turn(llm, "hello", first.interaction_id)
    await h.turn(llm, "blah again", first.interaction_id)

    assert h.rows == [] and h.state["clarification_attempts"] == 1


async def test_two_failed_turns_in_a_row_escalate(h, llm):
    llm.queue("nope", "nope", "nope", "nope")

    first = await h.turn(llm, "blah")
    await h.turn(llm, "blah blah", first.interaction_id)

    assert [r.reason for r in h.rows] == ["clarification_exhausted"]


# --- 6. owner keywords alert but keep the reply -------------------------------------------


async def test_an_owner_keyword_keeps_the_models_reply(h, llm):
    await h.service.save_settings(h.tenant, EscalationSettings(urgent_keywords=("sparkler",)))
    llm.queue(agent_turn("Thanks, I have noted that.", intent="UNKNOWN"))

    result = await h.turn(llm, "A sparkler fell on the chair")

    assert result.reply_text == "Thanks, I have noted that."
    assert result.reply_text != EMERGENCY_REPLY
    assert [(r.reason, r.urgency) for r in h.rows] == [("urgent", "high")]


# --- 2. one alert per interaction, through the service -------------------------------------


async def test_a_complaint_with_sms_off_does_not_mute_a_later_urgent_alert(h):
    interaction = uuid.uuid4()
    await h.service.save_settings(h.tenant, EscalationSettings())
    off = await h.service.escalate(h.tenant, interaction, _decision("complaint"))
    await h.service.save_settings(
        h.tenant, EscalationSettings(contact_phone=PHONE, sms_enabled=True)
    )

    urgent = await h.service.escalate(
        h.tenant, interaction, EscalationDecision("urgent", "high", ("safety_phrase",))
    )

    assert off.sms_status == "disabled"
    assert urgent.sms_status == "queued" and len(h.notifier.alerts) == 1


async def test_a_failed_first_alert_lets_the_next_reason_send(h):
    from services.notifier import NotifierError

    interaction = uuid.uuid4()
    await h.service.save_settings(
        h.tenant, EscalationSettings(contact_phone=PHONE, sms_enabled=True)
    )
    h.notifier.error = NotifierError("limit_reached")
    first = await h.service.escalate(h.tenant, interaction, _decision("complaint"))
    h.notifier.error = None

    second = await h.service.escalate(h.tenant, interaction, _decision("human_requested"))

    assert (first.sms_status, first.sms_error) == ("failed", "limit_reached")
    assert second.sms_status == "queued"


async def test_urgent_after_a_routine_alert_still_sends_but_not_the_reverse(h):
    interaction = uuid.uuid4()
    await h.service.save_settings(
        h.tenant, EscalationSettings(contact_phone=PHONE, sms_enabled=True)
    )
    routine = await h.service.escalate(h.tenant, interaction, _decision("complaint"))
    urgent = await h.service.escalate(
        h.tenant, interaction, EscalationDecision("urgent", "high", ("safety_phrase",))
    )
    after = await h.service.escalate(h.tenant, interaction, _decision("change_request"))

    assert (routine.sms_status, urgent.sms_status) == ("queued", "queued")
    assert after.sms_status == "skipped"
    assert len(h.notifier.alerts) == 2
    assert FakeLLMClient  # (keeps the import used when the module is trimmed)
