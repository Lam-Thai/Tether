"""`core/log_format`: JSON lines with the request context, redacted before writing."""

import io
import json
import logging
import sys
import uuid

import pytest

from core import log_context
from core.log_format import REDACTED, JsonFormatter, RedactionFilter, configure_logging, scrub


def _record(
    msg: str = "voice turn", *, level: int = logging.INFO, args: tuple = (), **extra: object
) -> logging.LogRecord:
    record = logging.makeLogRecord(
        {
            "name": "tether.voice",
            "levelno": level,
            "levelname": logging.getLevelName(level),
            "msg": msg,
            "args": args,
        }
    )
    record.__dict__.update(extra)
    return record


def _fake(*parts: str) -> str:
    """A key- or token-shaped fixture, assembled at runtime so this file holds no
    key-shaped literal (secret scanners rightly flag one; none of these were ever real)."""
    return "".join(parts)


def _with(key: str, value: object, *, level: int = logging.INFO) -> logging.LogRecord:
    """A record with one extra field whose name is a test parameter."""
    record = _record(level=level)
    setattr(record, key, value)
    return record


def _emit(record: logging.LogRecord) -> dict:
    RedactionFilter().filter(record)
    return json.loads(JsonFormatter().format(record))


def test_a_line_is_one_json_object_with_the_context_ids_and_the_extras():
    log_context.start_request("req-12345678")
    business_id, interaction_id = uuid.uuid4(), uuid.uuid4()
    log_context.bind(business_id=business_id, interaction_id=interaction_id)

    line = _emit(_record(input_mode="gemini_audio", stt_ms=840))

    assert line["level"] == "INFO"
    assert line["logger"] == "tether.voice"
    assert line["msg"] == "voice turn"
    assert line["ts"].endswith("+00:00")
    assert line["request_id"] == "req-12345678"
    assert line["business_id"] == str(business_id)
    assert line["interaction_id"] == str(interaction_id)
    assert line["input_mode"] == "gemini_audio"
    assert line["stt_ms"] == 840


def test_an_explicit_extra_id_wins_over_the_context():
    log_context.start_request("req-abcdefgh")
    log_context.bind(interaction_id=uuid.uuid4())
    explicit = str(uuid.uuid4())

    assert _emit(_record(interaction_id=explicit))["interaction_id"] == explicit


def test_ids_are_null_outside_a_request():
    log_context.start_request("req-00000000")
    log_context._request_id.set(None)

    line = _emit(_record())

    assert line["request_id"] is None
    assert line["business_id"] is None


@pytest.mark.parametrize(
    "key",
    [
        "token",
        "media_token",
        "refresh_token",
        "api_key",
        "apikey",
        "authorization",
        "client_secret",
        "password",
        "signature",
        "cookie",
        "credentials",
    ],
)
def test_secret_named_extras_are_redacted(key: str):
    assert _emit(_with(key, "s3cr3t-value"))[key] == REDACTED


@pytest.mark.parametrize("key", ["tokens_in", "tokens_out", "turn_key", "idempotency_key"])
def test_counts_and_non_secret_keys_are_kept(key: str):
    assert _emit(_with(key, 42))[key] == 42


@pytest.mark.parametrize("key", ["body", "text", "utterance", "transcript", "speech", "reply"])
def test_message_bodies_are_redacted_at_every_level(key: str):
    body = "I want a haircut tomorrow at 3"

    assert _emit(_with(key, body))[key] == REDACTED
    assert _emit(_with(key, body, level=logging.WARNING))[key] == REDACTED
    assert _emit(_with(key, body, level=logging.DEBUG))[key] == REDACTED


@pytest.mark.parametrize(
    ("raw", "leaked"),
    [
        (_fake("Authorization: Bear", "er abcdefgh", ".ijklmnop.qrstuvwx"), "abcdefgh.ijklmnop"),
        (
            _fake("token ey", "JhbGciOiJ", "SUzI1NiJ9.ey", "JzdWIiOiJ1c2VyIn0.c2lnbmF0dXJl here"),
            "hbGci",
        ),
        (_fake("key sk_", "test_", "51Habcdefghijklmnop"), "51Habcdefgh"),
        (_fake("key rk_", "live_", "abcdefghijklmnop"), "_abcdefgh"),
        (_fake("secret whsec_", "abcdefghijklmnop"), "_abcdefgh"),
        (_fake("gemini AIza", "SyA1234567890abcdefghijklmnopqrstuv"), "SyA1234567890"),
        (_fake("google ya29.", "a0AfH6SMBx-token"), "a0AfH6SMBx"),
        (_fake("refresh 1//", "0gabcdefghijklmnopqrstuvwx"), "0gabcdefgh"),
        ("GET /api/v1/media/AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-abcde HTTP/1.1", "AbCdEfGh"),
        ("https://x.test/cb?code=4/0Adeu5BW&state=eyJabc", "4/0Adeu5BW"),
        ("call from +15551234567 ended", "5551234567"),
        ("call from (555) 123-4567 ended", "123-4567"),
        ("GET /x?From=%2B15551234567&To=%2B15550001111", "5551234567"),
        ("card 4111 1111 1111 1111 declined", "4111 1111"),
        ("ssn 123-45-6789 given", "123-45-6789"),
        (
            "IntegrityError [SQL: INSERT INTO x VALUES ($1)]\n"
            "[parameters: ('my name is Jane, call me on 555 0100',)]\n"
            "(Background on this error at: https://sqlalche.me/e/20/gkpj)",
            "Jane",
        ),
    ],
    ids=[
        "bearer",
        "jwt",
        "stripe-secret",
        "stripe-restricted",
        "stripe-webhook",
        "google-api-key",
        "google-access-token",
        "google-refresh-token",
        "media-path",
        "oauth-query",
        "e164-phone",
        "nanp-phone",
        "url-encoded-phone",
        "card",
        "ssn",
        "db-parameters",
    ],
)
def test_scrub_removes_secrets_and_personal_data(raw: str, leaked: str):
    assert leaked not in scrub(raw)


def test_phone_numbers_keep_their_last_four_digits():
    assert scrub("caller +15551234567") == "caller ***4567"


def test_ids_times_prices_and_dates_are_untouched():
    text = (
        "booking 3f2b8c1e-9a4d-4e6f-8b2a-1c3d5e7f9a0b at 2026-10-29 14:30 for $45.00 "
        "latency_ms=1234 sid=CA0123456789abcdef0123456789abcdef"
    )
    assert scrub(text) == text


def test_the_message_and_its_arguments_are_scrubbed():
    line = _emit(
        _record("sms to %s failed: %s", args=("+15551234567", _fake("Bear", "er abcdefghijkl")))
    )

    assert line["msg"] == "sms to ***4567 failed: Bearer [redacted]"


def test_a_traceback_is_scrubbed():
    try:
        raise RuntimeError(
            _fake("lookup failed for +15551234567 with sk_", "test_abcdefghijklmnop")
        )
    except RuntimeError:
        record = _record("boom", level=logging.ERROR)
        record.exc_info = sys.exc_info()

    line = _emit(record)

    assert "Traceback" in line["exc"]
    assert "5551234567" not in line["exc"]
    assert "test_abcdefgh" not in line["exc"]


def test_redacting_twice_changes_nothing_more():
    record = _record(reason="phone +15551234567")
    RedactionFilter().filter(record)
    once = JsonFormatter().format(record)
    RedactionFilter().filter(record)

    assert JsonFormatter().format(record) == once


def test_configuring_twice_adds_one_handler_and_caplog_sees_redacted_records(caplog):
    configure_logging()
    configure_logging()

    tether = logging.getLogger("tether")
    handlers = [h for h in tether.handlers if h.get_name() == "tether-json"]
    assert len(handlers) == 1
    assert tether.propagate  # caplog still sees tether records
    for name in ("uvicorn.access", "uvicorn.error", "httpx"):
        filters = logging.getLogger(name).filters
        assert sum(isinstance(f, RedactionFilter) for f in filters) == 1

    with caplog.at_level(logging.INFO, logger="tether"):
        logging.getLogger("tether.test").info("caller %s", "+15551234567")

    assert "5551234567" not in caplog.text
    assert "***4567" in caplog.text


def test_the_handler_writes_json_lines():
    configure_logging()
    handler = next(h for h in logging.getLogger("tether").handlers if h.get_name() == "tether-json")
    assert isinstance(handler, logging.StreamHandler)
    stream = io.StringIO()
    previous = handler.setStream(stream)
    try:
        logging.getLogger("tether.test").warning("slow", extra={"latency_ms": 6000})
    finally:
        handler.setStream(previous)

    line = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert line["msg"] == "slow"
    assert line["latency_ms"] == 6000
