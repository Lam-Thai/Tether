"""`RequestContextMiddleware`: a request id per request, bound for logging and echoed back,
and one `tether.http` line with the route template, never the raw path."""

import logging

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from core import log_context
from core.app import create_app
from core.request_context import HEADER, RequestContextMiddleware

seen: dict[str, str | None] = {}


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/things/{secret_token}")
    async def thing(secret_token: str) -> dict[str, str]:
        seen.update(log_context.current())
        logging.getLogger("tether.test").info("inside")
        return {"ok": "yes"}

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    return app


async def _get(app: FastAPI, path: str, **headers: str):
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, headers=headers)


async def test_a_fresh_request_id_is_bound_and_echoed():
    response = await _get(_app(), "/things/abc")

    request_id = response.headers[HEADER]
    assert len(request_id) == 32
    assert seen["request_id"] == request_id
    assert seen["business_id"] is None


async def test_a_safe_incoming_request_id_is_kept():
    response = await _get(_app(), "/things/abc", **{"X-Request-ID": "trace-1234.abcd_EF"})

    assert response.headers[HEADER] == "trace-1234.abcd_EF"


@pytest.mark.parametrize(
    "unsafe",
    ['x" level="ERROR', "short", "a" * 65, "id with spaces"],
    ids=["injection", "too-short", "too-long", "spaces"],
)
async def test_an_unsafe_incoming_request_id_is_replaced(unsafe: str):
    response = await _get(_app(), "/things/abc", **{"X-Request-ID": unsafe})

    assert response.headers[HEADER] != unsafe
    assert len(response.headers[HEADER]) == 32


async def test_each_request_gets_its_own_id():
    app = _app()
    first = (await _get(app, "/things/a")).headers[HEADER]
    second = (await _get(app, "/things/b")).headers[HEADER]

    assert first != second


async def test_the_access_line_has_the_route_template_not_the_path(caplog):
    with caplog.at_level(logging.INFO, logger="tether"):
        response = await _get(_app(), "/things/very-secret-value-123")

    lines = [r for r in caplog.records if r.name == "tether.http"]
    assert len(lines) == 1
    line = lines[0]
    assert line.__dict__["route"] == "/things/{secret_token}"
    assert line.__dict__["method"] == "GET"
    assert line.__dict__["status"] == 200
    assert isinstance(line.__dict__["duration_ms"], int)
    assert "very-secret-value-123" not in caplog.text
    assert response.status_code == 200


async def test_an_unmatched_route_and_a_crash_are_logged_too(caplog):
    app = _app()
    with caplog.at_level(logging.INFO, logger="tether"):
        await _get(app, "/nope/hidden-path-value")
        await _get(app, "/boom")

    lines = [r.__dict__ for r in caplog.records if r.name == "tether.http"]
    assert [(line["route"], line["status"]) for line in lines] == [
        ("unmatched", 404),
        ("/boom", 500),
    ]
    assert "hidden-path-value" not in caplog.text


async def test_the_real_app_sends_a_request_id_and_exposes_it_to_the_browser():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert len(response.headers[HEADER]) == 32
