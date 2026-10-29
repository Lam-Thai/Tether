"""`HttpClerkUsersApi` against `httpx.MockTransport`: no network, real request shapes. The
secret key travels only in the Authorization header; every failure is a fixed reason code."""

import httpx
import pytest

from integrations.clerk.port import (
    REASON_AUTH_FAILED,
    REASON_INVALID_USER_ID,
    REASON_PROVIDER_ERROR,
    REASON_RATE_LIMITED,
    REASON_TIMEOUT,
    REASON_TRANSPORT,
    ClerkApiError,
    is_retryable,
)
from integrations.clerk.users import HttpClerkUsersApi

KEY = "sk_test_secretvalue123"
USER_ID = "user_2abcDEF456"
URL = f"https://api.clerk.com/v1/users/{USER_ID}"


class Recorder:
    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def api_with(*responses: httpx.Response | Exception) -> tuple[HttpClerkUsersApi, Recorder]:
    recorder = Recorder(*responses)
    api = HttpClerkUsersApi(KEY, 5.0, transport=httpx.MockTransport(recorder))
    return api, recorder


async def test_delete_sends_one_authenticated_delete_and_the_key_never_reaches_the_url():
    api, recorder = api_with(httpx.Response(200, json={"object": "deleted_user", "deleted": True}))

    await api.delete_user(USER_ID)

    (request,) = recorder.requests
    assert request.method == "DELETE"
    assert str(request.url) == URL
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in str(request.url)
    assert request.content == b""


async def test_a_missing_user_counts_as_deleted():
    api, recorder = api_with(httpx.Response(404, json={"errors": [{"code": "resource_not_found"}]}))

    await api.delete_user(USER_ID)

    assert len(recorder.requests) == 1


@pytest.mark.parametrize(
    ("status", "reason", "retryable"),
    [
        pytest.param(429, REASON_RATE_LIMITED, True, id="429"),
        pytest.param(408, REASON_TIMEOUT, True, id="408"),
        pytest.param(500, REASON_PROVIDER_ERROR, True, id="500"),
        pytest.param(503, REASON_PROVIDER_ERROR, True, id="503"),
        pytest.param(401, REASON_AUTH_FAILED, False, id="401"),
        pytest.param(403, REASON_AUTH_FAILED, False, id="403"),
        pytest.param(400, REASON_PROVIDER_ERROR, False, id="400"),
        pytest.param(422, REASON_PROVIDER_ERROR, False, id="422"),
    ],
)
async def test_http_failures_are_fixed_codes_with_the_right_retry_flag(status, reason, retryable):
    secret_text = "provider says: secret detail user@example.com"
    api, _ = api_with(httpx.Response(status, json={"errors": [{"message": secret_text}]}))

    with pytest.raises(ClerkApiError) as raised:
        await api.delete_user(USER_ID)

    assert raised.value.reason == reason
    assert raised.value.retryable is retryable
    assert is_retryable(raised.value) is retryable
    assert secret_text not in str(raised.value) and "example.com" not in str(raised.value)


async def test_a_timeout_and_a_transport_error_are_retryable():
    api, _ = api_with(httpx.ReadTimeout("slow"), httpx.ConnectError("refused"))

    with pytest.raises(ClerkApiError) as timed_out:
        await api.delete_user(USER_ID)
    with pytest.raises(ClerkApiError) as unreachable:
        await api.delete_user(USER_ID)

    assert (timed_out.value.reason, timed_out.value.retryable) == (REASON_TIMEOUT, True)
    assert (unreachable.value.reason, unreachable.value.retryable) == (REASON_TRANSPORT, True)
    assert KEY not in str(timed_out.value) + str(unreachable.value)


@pytest.mark.parametrize(
    "user_id",
    [
        pytest.param("", id="empty"),
        pytest.param("../organizations/org_1", id="path-traversal"),
        pytest.param("user_1/../../x", id="slash"),
        pytest.param("user_1?force=true", id="query"),
        pytest.param("org_2abc", id="wrong-prefix"),
        pytest.param("user_ 1", id="space"),
    ],
)
async def test_a_malformed_user_id_never_builds_a_url_or_calls_clerk(user_id):
    api, recorder = api_with()

    with pytest.raises(ClerkApiError) as raised:
        await api.delete_user(user_id)

    assert raised.value.reason == REASON_INVALID_USER_ID and not raised.value.retryable
    assert recorder.requests == []
