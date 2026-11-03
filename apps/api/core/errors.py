"""Consistent JSON error envelope + exception handlers.

Envelope shape (always):
    {"error": {"code": "<snake_case_code>", "message": "<human message>"}}
"""

import logging
from typing import cast

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("tether.api")


class AppError(Exception):
    """Base class for application errors that map to a specific HTTP status."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    # Extra response headers (e.g. `Retry-After` on a 429); set per instance by subclasses.
    headers: dict[str, str] | None = None

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class ServiceNameTakenError(AppError):
    """Another service of the same business already has this name (ignoring case).

    Raised by `repositories/services` when the unique index rejects a write, so it
    lives here where both the repository and the catalog service can import it."""

    status_code = status.HTTP_409_CONFLICT
    code = "service_name_taken"


def _envelope(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


# Handlers are typed against the base `Exception` to match FastAPI/Starlette's
# `ExceptionHandler` signature (it's invariant on the exception type), then
# cast to the concrete type each handler is actually registered for below.


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    app_error = cast(AppError, exc)
    return JSONResponse(
        status_code=app_error.status_code,
        content=_envelope(app_error.code, app_error.message),
        headers=app_error.headers,
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    http_exc = cast(StarletteHTTPException, exc)
    detail = http_exc.detail if isinstance(http_exc.detail, str) else "HTTP error"
    return JSONResponse(status_code=http_exc.status_code, content=_envelope("http_error", detail))


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=_envelope("validation_error", "Request validation failed."),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Never leak the raw exception message to the client; log it server-side.
    logger.exception("Unhandled exception while processing request", exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_envelope("internal_error", "An unexpected error occurred."),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
