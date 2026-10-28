"""The pure parts of `evals/stt_compare.py`: WER, percentiles, log parsing. No network."""

import pytest

from evals.stt_compare import (
    LatencyStats,
    latency_table,
    normalize_words,
    parse_log_lines,
    percentile,
    word_error_rate,
)


def test_normalization_lowercases_strips_punctuation_and_maps_numbers():
    assert normalize_words("Book at 3 p.m., please!") == ["book", "at", "three", "pm", "please"]
    assert normalize_words("3pm") == ["three", "pm"]
    assert normalize_words("Beard-trim") == ["beard", "trim"]
    assert normalize_words("20 and 21") == ["twenty", "and", "21"]


@pytest.mark.parametrize(
    ("ref", "hyp", "expected"),
    [
        ("I'd like a haircut", "id like a haircut", 0.0),
        ("at 3 pm", "at three p.m.", 0.0),
        ("one two three four", "one two four", 0.25),
        ("one two", "one too", 0.5),
        ("one two", "", 1.0),
        ("", "", 0.0),
        ("", "words", 1.0),
    ],
)
def test_word_error_rate(ref: str, hyp: str, expected: float):
    assert word_error_rate(ref, hyp) == pytest.approx(expected)


def test_percentile_is_nearest_rank():
    values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    assert percentile(values, 50) == 50
    assert percentile(values, 95) == 100
    assert percentile([7], 95) == 7
    assert percentile([], 50) is None
    assert percentile([3, 1, 2], 50) == 2


LOG = [
    "INFO tether.voice voice turn webhook=respond input_mode=twilio_stt latency_ms=900 tts_ms=300",
    "INFO tether.voice voice turn webhook=respond input_mode=gemini_audio fetch_ms=120 stt_ms=800 "
    "tts_ms=250 latency_ms=2000",
    "WARNING tether.voice voice turn slow webhook=respond input_mode=gemini_audio fetch_ms=200 "
    "stt_ms=1000 latency_ms=6000",
    "INFO tether.voice voice turn webhook=wait input_mode=gemini_audio latency_ms=50",
    "INFO tether.voice voice turn worker llm_ms=1500 turn=1",
    "INFO tether.voice voice turn worker llm_ms=notanumber",
    "INFO tether.voice voice turn webhook=respond input_mode=gemini_audio stt_ms=abc",
    "totally unrelated line stt_ms=1",
    "\x00\x01 garbage voice turn === ===",
]


def test_log_parsing_groups_by_mode_and_only_counts_respond_latency():
    stats = LatencyStats()
    parse_log_lines(LOG, stats, source="a.log")
    gem = stats.by_mode["gemini_audio"]
    assert gem["stt_ms"] == [800, 1000]
    assert gem["fetch_ms"] == [120, 200]
    assert gem["latency_ms"] == [2000, 6000]  # the wait webhook's 50 is excluded
    assert stats.by_mode["twilio_stt"]["latency_ms"] == [900]
    assert stats.by_mode["twilio_stt"]["stt_ms"] == []
    assert stats.llm_ms["a.log"] == [1500]


def test_the_table_is_markdown_and_tolerates_no_data():
    stats = LatencyStats()
    parse_log_lines(LOG, stats, source="a.log")
    table = latency_table(stats)
    assert "| gemini_audio | stt_ms | 800 | 1000 | 2 |" in table
    assert "| a.log | llm_ms | 1500 | 1500 | 1 |" in table
    assert "no voice turn lines" in latency_table(LatencyStats())
