"""`TwilioSmsNotifier` over `FakeSmsApi` and an in-memory usage store."""

import uuid

import pytest

from core.retry import RetryPolicy
from integrations.twilio.fake import FakeSmsApi
from integrations.twilio.port import TwilioApiError
from services.notifier import Alert, NotifierError, alert_body
from services.twilio_sms import TwilioSmsNotifier
from services.usage import DailyLimit, UsageKind
from tests.services.memory_usage_store import MemoryUsageStore, guard, limits

CALLBACK = "https://api.example.test/api/v1/webhooks/twilio/sms-status"
NO_WAIT = RetryPolicy(2, 0.0)


def alert() -> Alert:
    return Alert(
        business_id=uuid.uuid4(),
        interaction_id=uuid.uuid4(),
        escalation_id=uuid.uuid4(),
        reason="urgent",
        to="+15551230000",
        inbox_url="https://app.example.test/dashboard/inbox/abc",
    )


def notifier(api: FakeSmsApi, store: MemoryUsageStore, **daily: DailyLimit):
    return TwilioSmsNotifier(
        api,
        guard(store, limits(**daily) if daily else None),
        status_callback_url=CALLBACK,
        retry=NO_WAIT,
    )


async def test_sends_one_metered_sms_with_the_alert_body():
    api, store, a = FakeSmsApi(), MemoryUsageStore(), alert()

    result = await notifier(api, store).send_alert(a)

    assert (result.status, result.provider_id) == ("queued", api.sent[0].sid)
    sent = api.sent[0]
    assert sent.to == a.to
    assert sent.body == alert_body(a.reason, a.inbox_url)
    assert sent.status_callback == CALLBACK
    assert store.count(UsageKind.SMS, a.business_id) == 1
    assert store.count(UsageKind.SMS) == 1


async def test_a_rate_limit_is_retried_and_each_attempt_is_metered():
    api, store, a = FakeSmsApi(), MemoryUsageStore(), alert()
    api.fail_next(TwilioApiError("rate_limited", retryable=True))

    result = await notifier(api, store).send_alert(a)

    assert result.provider_id == api.sent[0].sid
    assert api.attempts == 2
    assert store.count(UsageKind.SMS, a.business_id) == 2


async def test_retries_are_bounded():
    api, store, a = FakeSmsApi(), MemoryUsageStore(), alert()
    api.fail_next(TwilioApiError("transport", retryable=True), times=5)

    with pytest.raises(NotifierError) as caught:
        await notifier(api, store).send_alert(a)

    assert caught.value.code == "transport"
    assert api.attempts == 2
    assert store.count(UsageKind.SMS, a.business_id) == 2


async def test_send_unconfirmed_is_not_retried():
    api, store, a = FakeSmsApi(), MemoryUsageStore(), alert()
    api.fail_next(TwilioApiError("send_unconfirmed"), times=2)

    with pytest.raises(NotifierError) as caught:
        await notifier(api, store).send_alert(a)

    assert caught.value.code == "send_unconfirmed"
    assert api.attempts == 1
    assert store.count(UsageKind.SMS, a.business_id) == 1


@pytest.mark.parametrize(
    "reason",
    ["invalid_number", "unverified_number", "auth_failed", "provider_error"],
)
async def test_other_provider_errors_pass_their_reason_through(reason):
    api, store = FakeSmsApi(), MemoryUsageStore()
    api.fail_next(TwilioApiError(reason, error_code="21211"))

    with pytest.raises(NotifierError) as caught:
        await notifier(api, store).send_alert(alert())

    assert caught.value.code == reason
    assert api.attempts == 1


async def test_a_reached_limit_sends_nothing():
    api, store = FakeSmsApi(), MemoryUsageStore()

    with pytest.raises(NotifierError) as caught:
        await notifier(
            api, store, sms=DailyLimit(per_day=10_000, per_business_per_day=0)
        ).send_alert(alert())

    assert caught.value.code == "limit_reached"
    assert api.attempts == 0
    assert store.count(UsageKind.SMS) == 0


async def test_an_unreadable_usage_store_fails_closed():
    api, store = FakeSmsApi(), MemoryUsageStore()
    store.fail_reserve = True

    with pytest.raises(NotifierError) as caught:
        await notifier(api, store).send_alert(alert())

    assert caught.value.code == "usage_unavailable"
    assert api.attempts == 0


async def test_an_unknown_provider_status_is_reported_as_accepted():
    api, store = FakeSmsApi(status="scheduled"), MemoryUsageStore()

    result = await notifier(api, store).send_alert(alert())

    assert result.status == "accepted"
