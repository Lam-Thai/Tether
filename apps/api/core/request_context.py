"""Request id + one access line per request, as plain ASGI middleware.

Each request gets a `request_id`: the caller's `X-Request-ID` when it is a safe token
(8-64 of `A-Za-z0-9._-`, so nothing a client sends can forge a log field), otherwise a
fresh one. It is bound into `core.log_context` for every log line of the request and
echoed back in the `X-Request-ID` response header. When the request finishes,
`tether.http` logs `method`, `route` (the matched route TEMPLATE, e.g.
`/api/v1/media/{media_token}`, never the raw path, which can carry a token), `status`
and `duration_ms`.
"""

import logging
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core import log_context

logger = logging.getLogger("tether.http")

HEADER = "x-request-id"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def request_id_from(headers: list[tuple[bytes, bytes]]) -> str:
    for name, value in headers:
        if name.lower() == HEADER.encode():
            candidate = value.decode("latin-1")
            if _SAFE_ID.match(candidate):
                return candidate
    return uuid.uuid4().hex


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = request_id_from(scope.get("headers", []))
        log_context.start_request(request_id)
        started = time.perf_counter()
        status = 500  # what the client gets if the app raises before responding

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                headers.append((HEADER.encode(), request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            route = scope.get("route")
            logger.info(
                "http request",
                extra={
                    "method": scope.get("method"),
                    "route": getattr(route, "path", None) or "unmatched",
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                },
            )
