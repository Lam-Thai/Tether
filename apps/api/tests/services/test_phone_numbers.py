"""`PhoneNumberService` on an in-memory store, `FakeNumbersApi` and `MemoryUsageStore`."""

import uuid
from datetime import UTC, datetime

import pytest

from core.retry import RetryPolicy
from core.tenant import Tenant
from integrations.twilio.fake import FakeNumbersApi
from integrations.twilio.port import REASON_PROVIDER_ERROR, REASON_TRANSPORT, TwilioApiError
from models.business import Business
from models.user import User
from services.agent import BusinessNotFoundError
from services.phone_numbers import (
    PhoneNumberInUseError,
    PhoneNumberNotFoundError,
    PhoneNumberService,
    TelephonyNotConfiguredError,
    TelephonyUnavailableError,
    WebhookTargets,
)
from services.usage import UsageKind
from tests.services.memory_phone_number_store import MemoryPhoneNumberStore
from tests.services.memory_usage_store import MemoryUsageStore, guard

VOICE = "https://api.example.test/api/v1/webhooks/twilio/voice"
STATUS = "https://api.example.test/api/v1/webhooks/twilio/voice/status"
TARGETS = WebhookTargets(VOICE, STATUS)
NOW = datetime(2026, 10, 21, 15, 30, tzinfo=UTC)
UNKNOWN_SID = "PN" + "0" * 32
ELSEWHERE = "https://elsewhere.example.test/voice"


def _tenant(name: str) -> Tenant:
    user = User(id=uuid.uuid4(), external_auth_id=f"u{uuid.uuid4().hex}", email=f"{name}@x.test")
    business = Business(
        id=uuid.uuid4(),
        owner_id=user.id,
        name=name,
        industry="Salon",
        address="1 Main St",
        timezone="UTC",
        phone=None,
        contact_email=None,
    )
    return Tenant(user=user, business=business)


def _retryable() -> TwilioApiError:
    return TwilioApiError(REASON_TRANSPORT, retryable=True)


def _bid(tenant: Tenant) -> uuid.UUID:
    assert tenant.business is not None
    return tenant.business.id


class Env:
    def __init__(self, *, configured: bool = True, targets: WebhookTargets = TARGETS) -> None:
        self.api = FakeNumbersApi()
        self.store = MemoryPhoneNumberStore()
        self.usage_store = MemoryUsageStore()
        self.service = PhoneNumberService(
            self.store,
            self.api if configured else None,
            guard(self.usage_store),
            targets,
            clock=lambda: NOW,
            retry=RetryPolicy(2, 0),
        )
        self.alpha = _tenant("alpha")
        self.beta = _tenant("beta")

    def metered_for(self, tenant: Tenant) -> int:
        return sum(
            1
            for e in self.usage_store.events
            if e.kind == UsageKind.TELEPHONY_API and e.business_id == _bid(tenant)
        )

    def op_count(self, op: str) -> int:
        return [name for name, _ in self.api.calls].count(op)


@pytest.fixture
def env() -> Env:
    return Env()


# --- overview -------------------------------------------------------------------------


async def test_overview_without_an_api_is_not_configured_and_calls_nothing():
    env = Env(configured=False)

    overview = await env.service.overview(env.alpha)

    assert overview.configured is False
    assert overview.available_error == "not_configured"
    assert overview.trial is None
    assert list(overview.available) == []
    assert env.api.calls == []


async def test_overview_with_unset_targets_is_not_configured_and_calls_nothing():
    env = Env(targets=WebhookTargets(None, None))

    overview = await env.service.overview(env.alpha)

    assert overview.configured is False
    assert overview.available_error == "not_configured"
    assert env.api.calls == []


async def test_overview_lists_only_numbers_no_business_holds(env):
    mine = env.api.add_number()
    theirs = env.api.add_number()
    free = env.api.add_number(friendly_name="Front desk")
    await env.service.associate(env.alpha, mine.sid)
    await env.service.associate(env.beta, theirs.sid)

    overview = await env.service.overview(env.alpha)

    assert overview.configured is True
    assert overview.available_error is None
    assert [n.provider_number_sid for n in overview.numbers] == [mine.sid]
    assert [
        (a.provider_number_sid, a.phone_number, a.friendly_name) for a in overview.available
    ] == [(free.sid, free.phone_number, "Front desk")]


@pytest.mark.parametrize("trial", [True, False], ids=["trial", "paid"])
async def test_overview_reports_the_account_type(trial):
    env = Env()
    env.api.trial = trial

    assert (await env.service.overview(env.alpha)).trial is trial


async def test_overview_trial_is_unknown_when_the_account_call_fails(env):
    env.api.add_number()
    env.api.fail_next("account", _retryable(), times=2)

    overview = await env.service.overview(env.alpha)

    assert overview.trial is None
    assert overview.available_error is None
    assert len(overview.available) == 1


async def test_overview_listing_failure_is_unavailable_after_the_retries(env):
    env.api.fail_next("list_numbers", _retryable(), times=2)

    overview = await env.service.overview(env.alpha)

    assert overview.configured is True
    assert overview.available_error == "unavailable"
    assert list(overview.available) == []
    assert env.op_count("list_numbers") == 2


async def test_overview_does_not_retry_a_permanent_error(env):
    env.api.fail_next("list_numbers", TwilioApiError(REASON_PROVIDER_ERROR))

    overview = await env.service.overview(env.alpha)

    assert overview.available_error == "unavailable"
    assert env.op_count("list_numbers") == 1


# --- associate ------------------------------------------------------------------------


async def test_associate_points_both_webhooks_and_marks_connected(env):
    number = env.api.add_number()

    row = await env.service.associate(env.alpha, number.sid)

    assert row.status == "connected"
    assert row.last_error is None
    assert row.verified_at == NOW
    assert row.phone_number == number.phone_number
    assert row.business_id == _bid(env.alpha)
    on_twilio = env.api.numbers[number.sid]
    assert on_twilio.voice_url == VOICE
    assert on_twilio.status_callback == STATUS


async def test_associate_checkpoints_before_calling_twilio(env):
    number = env.api.add_number()

    await env.service.associate(env.alpha, number.sid)

    assert env.store.checkpoints >= 1


async def test_associating_another_businesses_number_is_refused_before_any_twilio_call(env):
    number = env.api.add_number()
    await env.service.associate(env.beta, number.sid)
    env.api.calls.clear()

    with pytest.raises(PhoneNumberInUseError) as caught:
        await env.service.associate(env.alpha, number.sid)

    assert caught.value.status_code == 409
    assert env.api.calls == []
    assert await env.store.list_for_business(_bid(env.alpha)) == []
    assert len(await env.store.list_for_business(_bid(env.beta))) == 1


async def test_associating_my_own_number_again_is_idempotent(env):
    number = env.api.add_number()
    first = await env.service.associate(env.alpha, number.sid)

    again = await env.service.associate(env.alpha, number.sid)

    assert again.id == first.id
    assert len(await env.store.list_for_business(_bid(env.alpha))) == 1
    assert again.status == "connected"


async def test_associate_unknown_sid_is_not_found(env):
    with pytest.raises(PhoneNumberNotFoundError):
        await env.service.associate(env.alpha, UNKNOWN_SID)

    assert await env.store.list_for_business(_bid(env.alpha)) == []


async def test_associate_retries_a_transport_failure_once_then_succeeds(env):
    number = env.api.add_number()
    env.api.fail_next("get_number", _retryable())

    row = await env.service.associate(env.alpha, number.sid)

    assert row.status == "connected"
    assert env.op_count("get_number") == 2


async def test_associate_persistent_transport_failure_is_telephony_unavailable(env):
    number = env.api.add_number()
    env.api.fail_next("get_number", _retryable(), times=2)

    with pytest.raises(TelephonyUnavailableError) as caught:
        await env.service.associate(env.alpha, number.sid)

    assert caught.value.status_code == 503
    assert env.op_count("get_number") == 2
    assert await env.store.list_for_business(_bid(env.alpha)) == []


async def test_associate_when_not_configured_raises_not_configured():
    env = Env(configured=False)

    with pytest.raises(TelephonyNotConfiguredError):
        await env.service.associate(env.alpha, UNKNOWN_SID)

    assert env.api.calls == []


async def test_associate_without_a_business_is_refused(env):
    with pytest.raises(BusinessNotFoundError):
        await env.service.associate(Tenant(user=env.alpha.user, business=None), UNKNOWN_SID)


# --- test (re-verify) -----------------------------------------------------------------


async def test_test_reports_connected_and_refreshes_verified_at(env):
    number = env.api.add_number()
    row = await env.service.associate(env.alpha, number.sid)
    row.verified_at = None

    tested = await env.service.test(env.alpha, row.id)

    assert tested.status == "connected"
    assert tested.verified_at == NOW


async def test_test_voice_url_edited_in_the_console_is_misconfigured(env):
    number = env.api.add_number()
    row = await env.service.associate(env.alpha, number.sid)
    env.api.edit_externally(number.sid, voice_url=ELSEWHERE)

    tested = await env.service.test(env.alpha, row.id)

    assert (tested.status, tested.last_error) == ("misconfigured", "voice_url_mismatch")


async def test_test_status_callback_edited_in_the_console_is_misconfigured(env):
    number = env.api.add_number()
    row = await env.service.associate(env.alpha, number.sid)
    env.api.edit_externally(number.sid, status_callback="https://elsewhere.example.test/s")

    tested = await env.service.test(env.alpha, row.id)

    assert (tested.status, tested.last_error) == ("misconfigured", "status_callback_mismatch")


async def test_test_recovers_once_the_console_edit_is_undone(env):
    number = env.api.add_number()
    row = await env.service.associate(env.alpha, number.sid)
    env.api.edit_externally(number.sid, voice_url=ELSEWHERE)
    await env.service.test(env.alpha, row.id)
    env.api.edit_externally(number.sid, voice_url=VOICE)

    tested = await env.service.test(env.alpha, row.id)

    assert (tested.status, tested.last_error) == ("connected", None)


async def test_test_released_number_is_missing(env):
    number = env.api.add_number()
    row = await env.service.associate(env.alpha, number.sid)
    env.api.release(number.sid)

    tested = await env.service.test(env.alpha, row.id)

    assert (tested.status, tested.last_error) == ("misconfigured", "number_missing")


async def test_test_outage_keeps_the_status_and_flags_unavailable(env):
    number = env.api.add_number()
    row = await env.service.associate(env.alpha, number.sid)
    env.api.fail_next("get_number", _retryable(), times=2)

    tested = await env.service.test(env.alpha, row.id)

    assert tested.status == "connected"
    assert tested.last_error == "unavailable"


async def test_test_another_businesses_number_is_not_found_and_calls_nothing(env):
    number = env.api.add_number()
    theirs = await env.service.associate(env.beta, number.sid)
    env.api.calls.clear()

    with pytest.raises(PhoneNumberNotFoundError):
        await env.service.test(env.alpha, theirs.id)

    assert env.api.calls == []


# --- disconnect -----------------------------------------------------------------------


async def test_disconnect_clears_webhooks_pointing_here_and_deletes_the_row(env):
    number = env.api.add_number()
    row = await env.service.associate(env.alpha, number.sid)

    cleared = await env.service.disconnect(env.alpha, row.id)

    assert cleared is True
    assert env.api.numbers[number.sid].voice_url is None
    assert env.api.numbers[number.sid].status_callback is None
    assert await env.store.list_for_business(_bid(env.alpha)) == []


async def test_disconnect_does_not_wipe_a_number_repointed_elsewhere(env):
    number = env.api.add_number()
    row = await env.service.associate(env.alpha, number.sid)
    env.api.edit_externally(
        number.sid, voice_url=ELSEWHERE, status_callback="https://elsewhere.example.test/status"
    )
    env.api.calls.clear()

    cleared = await env.service.disconnect(env.alpha, row.id)

    assert cleared is True
    assert env.op_count("set_webhooks") == 0
    assert env.api.numbers[number.sid].voice_url == ELSEWHERE
    assert await env.store.list_for_business(_bid(env.alpha)) == []


async def test_disconnect_of_a_released_number_still_deletes_the_row(env):
    number = env.api.add_number()
    row = await env.service.associate(env.alpha, number.sid)
    env.api.release(number.sid)

    assert await env.service.disconnect(env.alpha, row.id) is True
    assert await env.store.list_for_business(_bid(env.alpha)) == []


async def test_disconnect_during_an_outage_deletes_the_row_and_reports_false(env):
    number = env.api.add_number()
    row = await env.service.associate(env.alpha, number.sid)
    env.api.fail_next("get_number", _retryable(), times=2)

    cleared = await env.service.disconnect(env.alpha, row.id)

    assert cleared is False
    assert await env.store.list_for_business(_bid(env.alpha)) == []


async def test_disconnect_another_businesses_number_is_not_found_and_nothing_is_deleted(env):
    number = env.api.add_number()
    theirs = await env.service.associate(env.beta, number.sid)
    env.api.calls.clear()

    with pytest.raises(PhoneNumberNotFoundError):
        await env.service.disconnect(env.alpha, theirs.id)

    assert env.api.calls == []
    assert len(await env.store.list_for_business(_bid(env.beta))) == 1
    assert env.api.numbers[number.sid].voice_url == VOICE


# --- metering -------------------------------------------------------------------------


async def test_every_twilio_attempt_is_a_telephony_usage_event_for_the_right_business(env):
    number = env.api.add_number()
    env.api.fail_next("get_number", _retryable())

    await env.service.associate(env.alpha, number.sid)

    # get_number x2 (one retry) + set_webhooks.
    assert len(env.api.calls) == 3
    assert env.metered_for(env.alpha) == 3
    assert env.metered_for(env.beta) == 0


async def test_overview_calls_are_metered_for_the_asking_business_including_retries(env):
    env.api.add_number()
    env.api.fail_next("list_numbers", _retryable())

    await env.service.overview(env.beta)

    # account, list_numbers x2.
    assert len(env.api.calls) == 3
    assert env.metered_for(env.beta) == 3
    assert env.metered_for(env.alpha) == 0


async def test_a_number_configured_for_something_else_is_neither_offered_nor_taken(env):
    elsewhere = env.api.add_number()
    env.api.edit_externally(elsewhere.sid, voice_url="https://other-app.example/voice")
    fresh_trial = env.api.add_number()
    env.api.edit_externally(fresh_trial.sid, voice_url="https://demo.twilio.com/welcome/voice/")

    overview = await env.service.overview(env.alpha)
    assert [a.provider_number_sid for a in overview.available] == [fresh_trial.sid]

    with pytest.raises(PhoneNumberInUseError):
        await env.service.associate(env.alpha, elsewhere.sid)
    assert env.op_count("set_webhooks") == 0
    assert env.api.numbers[elsewhere.sid].voice_url == "https://other-app.example/voice"

    connected = await env.service.associate(env.alpha, fresh_trial.sid)
    assert connected.status == "connected"


async def test_disconnect_keeps_a_voice_url_repointed_in_the_console_and_clears_only_ours(env):
    number = env.api.add_number()
    row = await env.service.associate(env.alpha, number.sid)
    env.api.edit_externally(number.sid, voice_url="https://other-app.example/voice")

    assert await env.service.disconnect(env.alpha, row.id) is True

    after = env.api.numbers[number.sid]
    assert after.voice_url == "https://other-app.example/voice"
    assert after.status_callback is None
