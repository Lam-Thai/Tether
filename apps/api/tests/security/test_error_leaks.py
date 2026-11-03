"""Error responses never carry internals: no stack trace, exception text, file path or echoed
input."""

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, Field

from core.app import create_app

ENVELOPE_KEYS = {"error"}


class Payload(BaseModel):
    email: str = Field(max_length=10)
    count: int


def build_app(secret: str) -> FastAPI:
    app = create_app()

    @app.get("/__explode__")
    async def explode() -> None:
        raise RuntimeError(f"secret-detail-{secret}")

    @app.post("/__validate__")
    async def validate(body: Payload) -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/__only_get__")
    async def only_get() -> dict[str, str]:
        return {"ok": "yes"}

    return app


@pytest.fixture
def secret() -> str:
    return uuid.uuid4().hex


@pytest.fixture
async def client(secret):
    # raise_app_exceptions=False: we want the 500 the real server would send, not the raise.
    transport = ASGITransport(app=build_app(secret), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def assert_clean(text: str) -> None:
    for marker in ("Traceback", 'File "', ".py", "RuntimeError", "site-packages", "line "):
        assert marker not in text, f"{marker!r} leaked in {text!r}"


async def test_an_unhandled_exception_is_a_generic_500_envelope(client, secret):
    response = await client.get("/__explode__")

    assert response.status_code == 500
    body = response.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["error"]["code"] == "internal_error"
    assert secret not in response.text
    assert "secret-detail" not in response.text
    assert_clean(response.text)
    assert not any(key.lower().startswith("x-debug") for key in response.headers)


async def test_a_validation_error_does_not_echo_the_submitted_values(client):
    leaked = "sk_live_" + uuid.uuid4().hex
    response = await client.post("/__validate__", json={"email": leaked, "count": leaked})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert leaked not in response.text
    assert set(response.json()) == ENVELOPE_KEYS
    assert_clean(response.text)


async def test_an_unparseable_body_does_not_echo_it_either(client):
    leaked = "password-" + uuid.uuid4().hex
    response = await client.post(
        "/__validate__",
        content='{"email": "' + leaked,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert leaked not in response.text


async def test_an_unknown_route_does_not_echo_the_path(client):
    marker = "probe-" + uuid.uuid4().hex
    response = await client.get(f"/api/v1/{marker}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_error"
    assert marker not in response.text


async def test_a_wrong_method_is_a_405_envelope(client):
    response = await client.post("/__only_get__")

    assert response.status_code == 405
    body = response.json()
    assert set(body) == ENVELOPE_KEYS
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == "http_error"
    assert_clean(response.text)


async def test_a_bad_token_is_a_401_that_does_not_echo_the_token(client):
    token = "eyJ" + uuid.uuid4().hex
    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert token not in response.text
    assert set(response.json()) == ENVELOPE_KEYS
