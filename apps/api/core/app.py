"""FastAPI application factory."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.health import router as health_router
from api.v1.router import router as v1_router
from core.config import settings
from core.errors import register_exception_handlers
from core.log_format import configure_logging
from core.rate_limit import warn_if_client_ip_unknown
from core.request_context import RequestContextMiddleware


def create_app() -> FastAPI:
    configure_logging()
    warn_if_client_ip_unknown()
    app = FastAPI(title="Tether API", version=settings.app_version)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    # Added last, so it runs outermost: even a refused CORS preflight gets a request id.
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(v1_router)

    return app
