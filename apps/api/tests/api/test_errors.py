"""Error envelope shape: {"error": {"code": ..., "message": ...}}."""

from httpx import ASGITransport, AsyncClient

from core.app import create_app


async def test_404_uses_error_envelope():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/this-route-does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert set(body.keys()) == {"error"}
    assert set(body["error"].keys()) == {"code", "message"}
    assert body["error"]["code"] == "http_error"


async def test_validation_error_uses_error_envelope():
    app = create_app()

    @app.get("/__test_validation__")
    async def _validation_probe(count: int):
        return {"count": count}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/__test_validation__", params={"count": "not-an-int"})

    assert response.status_code == 422
    body = response.json()
    assert set(body.keys()) == {"error"}
    assert body["error"]["code"] == "validation_error"
