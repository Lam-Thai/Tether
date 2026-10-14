"""`GET /health` — unversioned liveness/infra check, mounted at the app root.

Router responsibility only: parse the request, wire dependencies into the
service, shape the response. No business logic lives here.
"""

from functools import partial

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.db import get_session
from repositories.health import check_db_connection
from schemas.health import HealthResponse
from services.health import build_health_result

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def get_health(session: AsyncSession = Depends(get_session)) -> JSONResponse:
    result = await build_health_result(
        check_db=partial(check_db_connection, session),
        version=settings.app_version,
    )
    response = HealthResponse(status=result.status, db=result.db, version=result.version)
    status_code = 200 if result.db == "connected" else 503
    return JSONResponse(status_code=status_code, content=response.model_dump())
