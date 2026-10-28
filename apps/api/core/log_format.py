"""Log output for Tether's own loggers (`tether.*`).

uvicorn configures only its own loggers, so `tether.*` INFO lines (voice latency, TTS and
STT outcomes, refusals) were dropped and their `extra=` fields never printed anywhere.
`configure_logging` gives the `tether` logger one stderr handler that appends every extra
field as `key=value`, e.g.

    INFO tether.voice voice turn interaction_id=... input_mode=gemini_audio stt_ms=840

which is what `uv run python -m evals.stt_compare latency <log>` parses. Extras are ids,
fixed reason codes, counts and timings by convention (never caller text, phone numbers
or provider messages); this formatter adds no filtering of its own, so keep it that way.
Records still propagate, so pytest's `caplog` sees them as before.
"""

import logging

_HANDLER_NAME = "tether-key-value"
# Every attribute a plain LogRecord has; anything else on a record came from `extra=`.
_STANDARD = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


def _value(value: object) -> str:
    """Bare when unambiguous; quoted (Python repr, escapes included) when empty or when it
    holds whitespace, `=` or quotes, so a line always splits back into the same pairs."""
    text = str(value)
    if not text or any(c.isspace() or c in "=\"'" for c in text):
        return repr(text)
    return text


class KeyValueFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        extras = [
            f"{key}={_value(value)}"
            for key, value in record.__dict__.items()
            if key not in _STANDARD and not key.startswith("_")
        ]
        return f"{line} {' '.join(extras)}" if extras else line


def configure_logging(level: int = logging.INFO) -> None:
    """Idempotent: a second app (tests build several) adds no second handler."""
    logger = logging.getLogger("tether")
    logger.setLevel(level)
    if any(handler.get_name() == _HANDLER_NAME for handler in logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(KeyValueFormatter("%(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
