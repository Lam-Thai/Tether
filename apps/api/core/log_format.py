"""Structured log output: one JSON object per line, redacted before it is written.

uvicorn configures only its own loggers, so without this `tether.*` INFO lines (voice
latency, TTS and STT outcomes, refusals) were dropped. `configure_logging` gives the
`tether` logger one stderr handler that writes, e.g.

    {"ts": "2026-10-29T12:00:00.123Z", "level": "INFO", "logger": "tether.voice",
     "msg": "voice turn", "request_id": "...", "business_id": "...",
     "interaction_id": "...", "input_mode": "gemini_audio", "stt_ms": 840}

`request_id`, `business_id` and `interaction_id` come from `core.log_context` unless the
record sets them itself; every other `extra=` field follows. `evals.stt_compare latency`
reads these lines.

`RedactionFilter` runs on every record first, and changes the record itself, so later
handlers (pytest's `caplog`, uvicorn's) see the redacted text too:
- extras named like a secret (`token`, `secret`, `password`, `authorization`, `api_key`,
  `signature`, `cookie`, `credentials`, `*_token`, ...) become `[redacted]`;
- extras that carry a message body (`body`, `text`, `utterance`, `transcript`, `speech`,
  `content`, `prompt`, `reply`, ...) become `[redacted]`, at every level;
- every string (the message, its arguments, extras, a formatted traceback) is scrubbed:
  bearer tokens, JWTs, Stripe / Google keys and tokens, media tokens and secret query
  parameters in URLs, a database error's bound `[parameters: ...]`, card numbers, SSNs and
  security codes (`services.redaction`), and phone numbers (also URL-encoded, `%2B1...`),
  masked to the last four digits.
Extras should still be ids, fixed reason codes, counts and timings; the filter is the
safety net, not the convention.
"""

import json
import logging
import re
from datetime import UTC, datetime

from core import log_context
from services.redaction import redact

_HANDLER_NAME = "tether-json"
# Loggers outside `tether.*` that get the filter on the logger itself (it covers records
# logged directly to them, which is how these libraries log).
_FOREIGN_LOGGERS = ("uvicorn.access", "uvicorn.error", "httpx", "google_genai", "google.auth")
REDACTED = "[redacted]"
# Every attribute a plain LogRecord has; anything else on a record came from `extra=`.
_STANDARD = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}
_CONTEXT_KEYS = ("request_id", "business_id", "interaction_id")

_SECRET_KEY = re.compile(
    r"(?:^|_)(?:token|secret|password|passwd|authorization|api_?key|signature|cookie"
    r"|credentials?|private_key|dsn)(?:$|_)",
    re.IGNORECASE,
)
_BODY_KEYS = frozenset(
    {
        "body",
        "text",
        "message_text",
        "utterance",
        "transcript",
        "speech",
        "speech_result",
        "content",
        "prompt",
        "reply",
        "sms_body",
        "caller_text",
    }
)

# Order matters: whole tokens before the generic phone masking can split them.
_SCRUBS: tuple[tuple[re.Pattern[str], str], ...] = (
    # A database error quotes its bound values (customer text, phone numbers).
    (
        re.compile(r"\[parameters: .*?\](?=\s*(?:\(Background|\n|$))", re.DOTALL),
        "[parameters: redacted]",
    ),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [redacted]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"), "[jwt]"),
    (re.compile(r"\b(?:sk|rk|pk)_(?:test|live)_[A-Za-z0-9]{8,}"), REDACTED),
    (re.compile(r"\bwhsec_[A-Za-z0-9+/=]{8,}"), REDACTED),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), REDACTED),
    (re.compile(r"\bya29\.[0-9A-Za-z_.-]+"), REDACTED),
    (re.compile(r"\b1//[0-9A-Za-z_-]{20,}"), REDACTED),
    (re.compile(r"(/media/)[A-Za-z0-9_-]{16,}"), r"\1[redacted]"),
    (
        re.compile(
            r"(?i)([?&](?:token|code|state|key|signature|access_token|refresh_token"
            r"|client_secret|password)=)[^&\s\"']+"
        ),
        r"\1[redacted]",
    ),
)
# E.164 (`+15551234567`) and North American formats (`(555) 123-4567`, `555-123-4567`).
_E164 = re.compile(r"(?<![\w+])(?:\+|%2[Bb])\d{7,15}(?!\d)")
_NANP = re.compile(r"(?<![\w(])(?:\(\d{3}\)\s?|\d{3}[.-])\d{3}[.-]\d{4}(?!\d)")


def _mask(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group())
    return f"***{digits[-4:]}"


def scrub(text: str) -> str:
    """`text` with secrets removed and phone numbers masked; see the module docstring."""
    for pattern, replacement in _SCRUBS:
        text = pattern.sub(replacement, text)
    text = redact(text)
    text = _E164.sub(_mask, text)
    return _NANP.sub(_mask, text)


def _clean(key: str, value: object) -> object:
    if _SECRET_KEY.search(key):
        return REDACTED
    if key.lower() in _BODY_KEYS:
        return REDACTED
    if value is None or isinstance(value, bool | int | float):
        return value
    return scrub(str(value))


def _extras(record: logging.LogRecord) -> dict[str, object]:
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _STANDARD and not key.startswith("_")
    }


class RedactionFilter(logging.Filter):
    """Redacts a record in place (idempotent), then lets it through."""

    _exceptions = logging.Formatter()

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "_tether_redacted", False):
            return True
        record.msg = scrub(record.getMessage())
        record.args = None
        for key, value in _extras(record).items():
            setattr(record, key, _clean(key, value))
        if record.exc_info and not record.exc_text:
            record.exc_text = self._exceptions.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = scrub(record.exc_text)
        if record.stack_info:
            record.stack_info = scrub(record.stack_info)
        record._tether_redacted = True
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extras = _extras(record)
        context = log_context.current()
        line: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in _CONTEXT_KEYS:
            line[key] = extras.pop(key, None) or context[key]
        line.update(extras)
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            line["exc"] = record.exc_text
        if record.stack_info:
            line["stack"] = record.stack_info
        return json.dumps(line, default=str, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    """Idempotent: a second app (tests build several) adds no second handler or filter.

    Other libraries' loggers that can print URLs or request details get the redaction
    filter too: uvicorn's access log prints request paths (a media URL's path is its
    token), httpx logs request URLs at INFO, the Google SDKs log request details."""
    logger = logging.getLogger("tether")
    logger.setLevel(level)
    if not any(handler.get_name() == _HANDLER_NAME for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.set_name(_HANDLER_NAME)
        handler.addFilter(RedactionFilter())
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    for name in _FOREIGN_LOGGERS:
        other = logging.getLogger(name)
        if not any(isinstance(f, RedactionFilter) for f in other.filters):
            other.addFilter(RedactionFilter())
