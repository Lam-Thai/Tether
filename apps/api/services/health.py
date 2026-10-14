"""Health service: the deep module behind `GET /health`.

No FastAPI or SQLAlchemy imports here — this is pure logic, unit-testable
with a fake `check_db` callable and no real database.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

HealthCheck = Callable[[], Awaitable[bool]]


@dataclass(frozen=True)
class HealthResult:
    status: Literal["ok", "degraded"]
    db: Literal["connected", "error"]
    version: str


async def build_health_result(check_db: HealthCheck, version: str) -> HealthResult:
    db_ok = await check_db()
    if db_ok:
        return HealthResult(status="ok", db="connected", version=version)
    return HealthResult(status="degraded", db="error", version=version)
