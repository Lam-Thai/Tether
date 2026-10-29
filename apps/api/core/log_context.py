"""Per-request log context: `request_id`, `business_id`, `interaction_id`.

Context variables, not globals: each request (and each task it starts, which copies the
context) sees only its own ids, so concurrent requests never mix them up. The request
middleware (`core/request_context.py`) starts a fresh context per request; `get_tenant`
binds the business and the conversation engine binds the interaction once it has one.
`core/log_format` stamps all three on every record. Ids only, never free text.
"""

import uuid
from contextvars import ContextVar

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_business_id: ContextVar[str | None] = ContextVar("business_id", default=None)
_interaction_id: ContextVar[str | None] = ContextVar("interaction_id", default=None)


def start_request(request_id: str) -> None:
    """A new request: its id, and no business or interaction yet."""
    _request_id.set(request_id)
    _business_id.set(None)
    _interaction_id.set(None)


def bind(*, business_id: uuid.UUID | None = None, interaction_id: uuid.UUID | None = None) -> None:
    if business_id is not None:
        _business_id.set(str(business_id))
    if interaction_id is not None:
        _interaction_id.set(str(interaction_id))


def current() -> dict[str, str | None]:
    return {
        "request_id": _request_id.get(),
        "business_id": _business_id.get(),
        "interaction_id": _interaction_id.get(),
    }
