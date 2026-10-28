"""`KeyValueFormatter` / `configure_logging`: extras become `key=value`, once."""

import logging

from core.log_format import KeyValueFormatter, configure_logging


def _record(**extra: object) -> logging.LogRecord:
    record = logging.makeLogRecord(
        {"name": "tether.voice", "levelname": "INFO", "msg": "voice turn"}
    )
    record.__dict__.update(extra)
    return record


def test_extras_are_appended_as_key_value_pairs():
    formatter = KeyValueFormatter("%(levelname)s %(name)s %(message)s")

    line = formatter.format(_record(input_mode="gemini_audio", stt_ms=840, interaction_id=None))

    assert (
        line
        == "INFO tether.voice voice turn input_mode=gemini_audio stt_ms=840 interaction_id=None"
    )


def test_a_value_with_spaces_or_an_empty_value_is_quoted():
    line = KeyValueFormatter("%(message)s").format(_record(reason="two words", code=""))

    assert line == "voice turn reason='two words' code=''"


def test_equals_signs_and_quotes_are_quoted_so_pairs_stay_unambiguous():
    line = KeyValueFormatter("%(message)s").format(_record(a="x=1", b='say "hi"'))

    assert line == "voice turn a='x=1' b='say \"hi\"'"


def test_a_record_without_extras_is_unchanged():
    assert KeyValueFormatter("%(message)s").format(_record()) == "voice turn"


def test_configuring_twice_adds_one_handler():
    configure_logging()
    configure_logging()

    handlers = [
        h for h in logging.getLogger("tether").handlers if h.get_name() == "tether-key-value"
    ]
    assert len(handlers) == 1
    assert logging.getLogger("tether").propagate  # caplog still sees tether records
