"""Shared helpers for the provider-failure tests: leak checks and stored-record reads."""

import logging
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from core.db import async_session_factory
from models.booking import Booking
from models.interaction import Interaction, InteractionMessage
from models.operation import InteractionOperation
from tests.chaos.chaos import CANARY

# Text a customer typed and numbers that must never reach a log record.
CUSTOMER_CANARY = "CUSTOMER-CANARY-81c2"
CUSTOMER_PHONE = "+15557654321"
CUSTOMER_TEXT = (
    f"Hi, I'd like a haircut. My code is {CUSTOMER_CANARY}, call me on {CUSTOMER_PHONE}."
)
OWNER_PHONE = "+15551234567"


def auth(tenant) -> dict[str, str]:
    return {"Authorization": f"Bearer {tenant.token}"}


async def chat(
    client, tenant, message: str = CUSTOMER_TEXT, interaction_id: str | None = None
) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/agent/test",
        json={"interaction_id": interaction_id, "message": message},
        headers=auth(tenant),
    )
    assert response.status_code == 200, response.text  # a provider failure is never a 500
    assert CANARY not in response.text
    return response.json()


async def stored_messages(client, tenant, interaction_id: str) -> list[dict[str, Any]]:
    response = await client.get(
        f"/api/v1/interactions/{interaction_id}/messages", headers=auth(tenant)
    )
    assert response.status_code == 200, response.text
    assert CANARY not in response.text
    return response.json()["items"]


async def interaction_state(interaction_id: str) -> dict[str, Any]:
    async with async_session_factory() as session:
        row = await session.get(Interaction, uuid.UUID(interaction_id))
        assert row is not None, "the interaction was lost"
        return dict(row.state)


async def booking_statuses(business_id: uuid.UUID) -> list[str]:
    async with async_session_factory() as session:
        rows = await session.execute(
            select(Booking.status).where(Booking.business_id == business_id)
        )
        return sorted(rows.scalars())


async def operations(interaction_id: str, tool: str) -> list[InteractionOperation]:
    async with async_session_factory() as session:
        rows = await session.execute(
            select(InteractionOperation)
            .where(
                InteractionOperation.interaction_id == uuid.UUID(interaction_id),
                InteractionOperation.tool_name == tool,
            )
            .order_by(InteractionOperation.created_at)
        )
        return list(rows.scalars())


async def customer_message_count(interaction_id: str) -> int:
    async with async_session_factory() as session:
        rows = await session.execute(
            select(InteractionMessage.id).where(
                InteractionMessage.interaction_id == uuid.UUID(interaction_id),
                InteractionMessage.speaker == "customer",
            )
        )
        return len(list(rows.scalars()))


def _rendered(record: logging.LogRecord) -> str:
    """Everything a log line could carry: message, extras, and the full traceback chain."""
    return f"{logging.Formatter().format(record)} {record.__dict__}"


def tether_records(caplog: pytest.LogCaptureFixture, logger: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == logger]


def assert_logs_are_clean(caplog: pytest.LogCaptureFixture, *private: str) -> None:
    """No record anywhere carries provider error text, and no `tether.*` record carries
    anything in `private` (customer text, phone numbers)."""
    assert caplog.records, "expected the failure to be logged"
    for record in caplog.records:
        assert CANARY not in _rendered(record), f"provider text in {record.name}"
        if record.name == "tether" or record.name.startswith("tether."):
            rendered = _rendered(record)
            for secret in private:
                assert secret not in rendered, f"{secret!r} leaked into {record.name}"
