"""`EscalationService` on an in-memory store and a recording fake notifier."""

import asyncio
import logging
import uuid

import pytest

from core.tenant import Tenant
from models.business import Business
from models.user import User
from services.escalation import (
    ContactPhoneRequiredError,
    EscalationNotFoundError,
    EscalationService,
    EscalationSettings,
    InvalidEscalationSettingsError,
)
from services.escalation_policy import EscalationDecision, Reason
from services.notifier import Alert, LogNotifier, NotifierError, SendResult
from tests.services.memory_escalation_store import MemoryEscalationStore

PHONE = "+15551234567"


class RecordingNotifier:
    def __init__(self) -> None:
        self.alerts: list[Alert] = []
        self.error: Exception | None = None
        self.result = SendResult(status="queued", provider_id="SM001")

    async def send_alert(self, alert: Alert) -> SendResult:
        self.alerts.append(alert)
        if self.error is not None:
            raise self.error
        return self.result


def _tenant() -> Tenant:
    user = User(id=uuid.uuid4(), external_auth_id="u", email="o@example.com")
    business = Business(
        id=uuid.uuid4(),
        owner_id=user.id,
        name="Alpha",
        industry="Salon",
        address="1 Main St",
        timezone="UTC",
        phone=None,
        contact_email=None,
    )
    return Tenant(user=user, business=business)


def _decision(reason: Reason = "human_requested") -> EscalationDecision:
    return EscalationDecision(reason=reason, urgency="normal", signals=())


@pytest.fixture
def tenant() -> Tenant:
    return _tenant()


@pytest.fixture
def store() -> MemoryEscalationStore:
    return MemoryEscalationStore()


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()


def _service(store, notifier) -> EscalationService:
    return EscalationService(store, notifier, inbox_url=lambda i: f"https://app.test/inbox/{i}")


async def _enable_sms(service, tenant) -> None:
    await service.save_settings(tenant, EscalationSettings(contact_phone=PHONE, sms_enabled=True))


async def test_n_escalations_for_one_reason_is_one_record_and_one_send(tenant, store, notifier):
    service = _service(store, notifier)
    await _enable_sms(service, tenant)
    interaction = uuid.uuid4()

    results = [await service.escalate(tenant, interaction, _decision()) for _ in range(5)]

    assert len(store.rows) == 1
    assert len(notifier.alerts) == 1
    alert = notifier.alerts[0]
    assert alert.to == PHONE and alert.inbox_url == f"https://app.test/inbox/{interaction}"
    assert results[0].sms_status == "queued" and results[0].sms_sid == "SM001"


async def test_second_reason_in_one_interaction_is_skipped_with_no_second_send(
    tenant, store, notifier
):
    service = _service(store, notifier)
    await _enable_sms(service, tenant)
    interaction = uuid.uuid4()

    first = await service.escalate(tenant, interaction, _decision("human_requested"))
    second = await service.escalate(tenant, interaction, _decision("complaint"))

    assert first.sms_status == "queued"
    assert second.sms_status == "skipped"
    assert len(store.rows) == 2 and len(notifier.alerts) == 1


@pytest.mark.parametrize(
    "settings",
    [
        EscalationSettings(),
        EscalationSettings(contact_phone=PHONE, sms_enabled=False),
    ],
    ids=["defaults", "sms-off"],
)
async def test_sms_off_is_disabled_and_nothing_is_sent(tenant, store, notifier, settings):
    service = _service(store, notifier)
    await service.save_settings(tenant, settings)

    record = await service.escalate(tenant, uuid.uuid4(), _decision())

    assert record.sms_status == "disabled" and notifier.alerts == []


async def test_demo_notifier_marks_logged(tenant, store):
    service = _service(store, LogNotifier())
    await _enable_sms(service, tenant)

    record = await service.escalate(tenant, uuid.uuid4(), _decision())

    assert record.sms_status == "logged" and record.sms_sid is None


async def test_notifier_failure_keeps_the_record_and_logs_a_code(tenant, store, notifier, caplog):
    notifier.error = NotifierError("invalid_number")
    service = _service(store, notifier)
    await _enable_sms(service, tenant)

    with caplog.at_level(logging.WARNING, logger="tether.escalation"):
        record = await service.escalate(tenant, uuid.uuid4(), _decision())

    assert record.sms_status == "failed" and record.sms_error == "invalid_number"
    assert len(store.rows) == 1
    assert any(getattr(r, "sms_error", None) == "invalid_number" for r in caplog.records)
    assert PHONE not in caplog.text


@pytest.mark.parametrize("code", ["limit_reached", "usage_unavailable", "send_unconfirmed"])
async def test_fixed_failure_codes_are_stored(tenant, store, notifier, code):
    notifier.error = NotifierError(code)
    service = _service(store, notifier)
    await _enable_sms(service, tenant)

    record = await service.escalate(tenant, uuid.uuid4(), _decision())

    assert (record.sms_status, record.sms_error) == ("failed", code)


async def test_unexpected_notifier_exception_never_escapes(tenant, store, notifier):
    notifier.error = RuntimeError("boom")
    service = _service(store, notifier)
    await _enable_sms(service, tenant)

    record = await service.escalate(tenant, uuid.uuid4(), _decision())

    assert (record.sms_status, record.sms_error) == ("failed", "provider_error")


async def test_concurrent_notify_sends_once(tenant, store, notifier):
    service = _service(store, notifier)
    await _enable_sms(service, tenant)
    recorded = await service.record(tenant, uuid.uuid4(), _decision())

    await asyncio.gather(*(service.notify(tenant, recorded) for _ in range(4)))

    assert len(notifier.alerts) == 1


async def test_notify_ignores_a_repeat(tenant, store, notifier):
    service = _service(store, notifier)
    await _enable_sms(service, tenant)
    interaction = uuid.uuid4()
    await service.record(tenant, interaction, _decision())
    repeat = await service.record(tenant, interaction, _decision())

    assert repeat.created is False
    await service.notify(tenant, repeat)
    assert notifier.alerts == []


@pytest.mark.parametrize(
    ("sequence", "final"),
    [
        (["queued", "sent", "delivered"], "delivered"),
        (["delivered", "sent", "queued"], "delivered"),
        (["sent", "sent"], "sent"),
        (["undelivered", "delivered"], "undelivered"),
        (["delivered", "failed"], "delivered"),
    ],
    ids=["forward", "backward-ignored", "repeat", "terminal-sticks", "terminal-not-replaced"],
)
async def test_apply_sms_status_is_monotonic(tenant, store, notifier, sequence, final):
    notifier.result = SendResult(status="accepted", provider_id="SM9")
    service = _service(store, notifier)
    await _enable_sms(service, tenant)
    await service.escalate(tenant, uuid.uuid4(), _decision())

    for status in sequence:
        await service.apply_sms_status("SM9", status)

    assert next(iter(store.rows.values())).sms_status == final


async def test_apply_sms_status_stores_the_numeric_error_code_only(tenant, store, notifier):
    notifier.result = SendResult(status="sent", provider_id="SM7")
    service = _service(store, notifier)
    await _enable_sms(service, tenant)
    await service.escalate(tenant, uuid.uuid4(), _decision())

    assert await service.apply_sms_status("SM7", "undelivered", "30003") is True

    row = next(iter(store.rows.values()))
    assert (row.sms_status, row.sms_error) == ("undelivered", "30003")


async def test_unknown_sid_and_unknown_status_are_no_ops(tenant, store, notifier):
    service = _service(store, notifier)
    assert await service.apply_sms_status("SM_nope", "delivered") is False
    await _enable_sms(service, tenant)
    await service.escalate(tenant, uuid.uuid4(), _decision())
    assert await service.apply_sms_status("SM001", "receiving") is False


async def test_resolve_is_idempotent_and_scoped(tenant, store, notifier):
    service = _service(store, notifier)
    record = await service.escalate(tenant, uuid.uuid4(), _decision())

    first = await service.resolve(tenant, record.id)
    again = await service.resolve(tenant, record.id)
    assert first.status == "resolved" and again.resolved_at == first.resolved_at

    with pytest.raises(EscalationNotFoundError):
        await service.resolve(_tenant(), record.id)
    assert await service.list(tenant, "open") == []
    assert len(await service.list(tenant, "all")) == 1


async def test_settings_defaults_and_validation(tenant, store, notifier):
    service = _service(store, notifier)
    view = await service.get_settings(tenant)
    assert view.customized is False
    assert (view.settings.sms_enabled, view.settings.clarification_threshold) == (False, 2)

    with pytest.raises(ContactPhoneRequiredError):
        await service.save_settings(tenant, EscalationSettings(sms_enabled=True))
    for bad in (
        EscalationSettings(contact_phone="12345"),
        EscalationSettings(clarification_threshold=0),
        EscalationSettings(clarification_threshold=6),
        EscalationSettings(urgent_keywords=("x" * 41,)),
        EscalationSettings(urgent_keywords=tuple(f"k{i}" for i in range(21))),
        EscalationSettings(urgent_keywords=("  ",)),
    ):
        with pytest.raises(InvalidEscalationSettingsError):
            await service.save_settings(tenant, bad)

    saved = await service.save_settings(
        tenant, EscalationSettings(urgent_keywords=(" Gas  Leak ", "gas leak", "Fire"))
    )
    assert saved.customized and saved.settings.urgent_keywords == ("gas leak", "fire")
    rules = await service.rules(tenant)
    assert rules.urgent_keywords == ("gas leak", "fire")
