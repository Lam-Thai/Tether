from services.health import build_health_result


async def test_build_health_result_when_db_check_succeeds():
    async def fake_check_db_ok() -> bool:
        return True

    result = await build_health_result(check_db=fake_check_db_ok, version="1.2.3")

    assert result.status == "ok"
    assert result.db == "connected"
    assert result.version == "1.2.3"


async def test_build_health_result_when_db_check_fails():
    async def fake_check_db_down() -> bool:
        return False

    result = await build_health_result(check_db=fake_check_db_down, version="1.2.3")

    assert result.status == "degraded"
    assert result.db == "error"
    assert result.version == "1.2.3"
