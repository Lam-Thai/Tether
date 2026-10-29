"""`MeteredIdentityDirectory`: every Clerk call is metered (never capped, never attributed to
a business), retried only when the adapter flags it, and failures are fixed reason codes."""

import pytest

from integrations.clerk.fake import FakeClerkUsersApi
from integrations.clerk.port import REASON_AUTH_FAILED, REASON_TRANSPORT, ClerkApiError
from services.identity import IdentityError, MeteredIdentityDirectory, UnconfiguredIdentityDirectory
from services.usage import UsageKind
from tests.services.memory_usage_store import MemoryUsageStore, guard

USER = "user_2abc"


async def test_a_delete_is_one_metered_call_attributed_to_no_business():
    store, api = MemoryUsageStore(), FakeClerkUsersApi()

    await MeteredIdentityDirectory(api, guard(store)).delete_user(USER)

    assert api.deleted == [USER]
    assert store.count(UsageKind.IDENTITY_CALL) == 1
    assert [e.business_id for e in store.events] == [None]
    assert store.events[0].provider == "clerk"


async def test_a_retryable_failure_is_retried_once_and_every_attempt_is_counted():
    store, api = MemoryUsageStore(), FakeClerkUsersApi()
    api.fail_next(ClerkApiError(REASON_TRANSPORT, retryable=True))

    await MeteredIdentityDirectory(api, guard(store)).delete_user(USER)

    assert api.calls == [USER, USER] and api.deleted == [USER]
    assert store.count(UsageKind.IDENTITY_CALL) == 2
    assert store.count(UsageKind.FAILED_REQUEST) == 1


async def test_retries_are_bounded():
    store, api = MemoryUsageStore(), FakeClerkUsersApi()
    api.fail_next(ClerkApiError(REASON_TRANSPORT, retryable=True), times=5)

    with pytest.raises(IdentityError) as raised:
        await MeteredIdentityDirectory(api, guard(store)).delete_user(USER)

    assert raised.value.reason == REASON_TRANSPORT
    assert len(api.calls) == 2  # IDENTITY_RETRY: two attempts, never more


async def test_a_non_retryable_failure_is_a_fixed_code_after_one_attempt():
    store, api = MemoryUsageStore(), FakeClerkUsersApi()
    api.fail_next(ClerkApiError(REASON_AUTH_FAILED))

    with pytest.raises(IdentityError) as raised:
        await MeteredIdentityDirectory(api, guard(store)).delete_user(USER)

    assert raised.value.reason == REASON_AUTH_FAILED
    assert len(api.calls) == 1


async def test_an_unreadable_usage_store_does_not_block_a_metered_only_kind():
    store, api = MemoryUsageStore(), FakeClerkUsersApi()
    store.fail_reserve = True  # a metered-only kind fails open

    await MeteredIdentityDirectory(api, guard(store)).delete_user(USER)

    assert api.deleted == [USER]


async def test_unconfigured_reports_not_configured_without_a_call():
    with pytest.raises(IdentityError) as raised:
        await UnconfiguredIdentityDirectory().delete_user(USER)

    assert raised.value.reason == "not_configured"
