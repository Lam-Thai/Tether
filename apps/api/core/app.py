"""FastAPI application factory."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.health import router as health_router
from api.v1.router import router as v1_router
from core.config import settings
from core.errors import register_exception_handlers
from core.log_format import configure_logging


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Tether API", version=settings.app_version)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(v1_router)

    return app
