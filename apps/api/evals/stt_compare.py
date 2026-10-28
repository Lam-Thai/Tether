"""Dev tool: compare speech-to-text paths. Not part of CI, `git push` or the eval suites.

    uv run python -m evals.stt_compare accuracy [--max-utterances 5] [--voice Kore]
    uv run python -m evals.stt_compare latency API.log [MORE.log ...]

`accuracy` speaks synthetic salon utterances with the real Gemini TTS, degrades them to phone
quality exactly like a call (24 kHz PCM -> 8 kHz mu-law WAV -> 8 kHz 16-bit PCM WAV, what
Twilio's recording looks like), transcribes them with the real `GeminiSpeechToText` and
reports word error rate and latency. These calls deliberately BYPASS `UsageGuard` (this is a
dev tool, not the product path) but still spend the key's real free-tier requests, so the
budget (N TTS + N STT requests) is printed first and the run stops on the first 429.
Refuses `ENVIRONMENT=production` and a missing `GEMINI_API_KEY`; the key is never printed.

`latency` reads API log files and prints a markdown table of p50/p95 per `input_mode`
for lines like `voice turn ... key=value ...`, plus `llm_ms` from `voice turn worker` lines.
Exit codes: 0 ok, 3 refused or bad input, 4 crashed.
"""

import argparse
import asyncio
import math
import re
import sys
import time
from collections import defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path

REFUSED = 3
CRASHED = 4
MAX_UTTERANCES = 10
DEFAULT_UTTERANCES = 5

# Synthetic only, written for the demo salon.
UTTERANCES: tuple[str, ...] = (
    "I'd like to book a haircut tomorrow at 3 pm.",
    "What time do you close on Saturday?",
    "How much is a beard trim?",
    "My name is Priya Raghunathan, that's P R I Y A.",
    "You can reach me at 5 5 5 0 1 4 2.",
    "Do you have anything open on Friday morning?",
    "I need to move my appointment to next week.",
    "Is there parking near the salon?",
)

_NUMBER_WORDS = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty"
).split()
_NUMBER_BY_DIGITS = {str(i): word for i, word in enumerate(_NUMBER_WORDS)}
_PUNCT = re.compile(r"[^a-z0-9\s]")
_DIGIT_LETTER = re.compile(r"(?<=\d)(?=[a-z])|(?<=[a-z])(?=\d)")
_KV = re.compile(r"(\w+)=(\S+)")
LATENCY_FIELDS = ("fetch_ms", "stt_ms", "tts_ms", "latency_ms")


# --- pure parts -------------------------------------------------------------------------


def normalize_words(text: str) -> list[str]:
    """Lowercase words with punctuation gone, "p.m." as "pm", and 0-20 as words."""
    lowered = text.lower().replace("p.m.", "pm").replace("a.m.", "am").replace("-", " ")
    lowered = _PUNCT.sub("", lowered)
    lowered = _DIGIT_LETTER.sub(" ", lowered)
    return [_NUMBER_BY_DIGITS.get(word, word) for word in lowered.split()]


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Word-level edit distance over the reference length (0.0 = identical)."""
    ref, hyp = normalize_words(reference), normalize_words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        current = [i]
        for j, h in enumerate(hyp, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (r != h)))
        previous = current
    return previous[-1] / len(ref)


def percentile(values: Sequence[float], pct: float) -> float | None:
    """Nearest-rank percentile; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _fields(line: str) -> dict[str, str]:
    return dict(_KV.findall(line))


def _number(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


class LatencyStats:
    """Samples per input mode, plus `llm_ms` samples per file."""

    def __init__(self) -> None:
        self.by_mode: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: {field: [] for field in LATENCY_FIELDS}
        )
        self.llm_ms: dict[str, list[float]] = defaultdict(list)


def parse_log_lines(lines: Iterable[str], stats: LatencyStats, source: str = "log") -> None:
    for line in lines:
        if "voice turn" not in line:
            continue
        fields = _fields(line)
        if "voice turn worker" in line:
            llm = _number(fields.get("llm_ms"))
            if llm is not None:
                stats.llm_ms[source].append(llm)
            continue
        mode = fields.get("input_mode", "unknown")
        for name in LATENCY_FIELDS:
            if name == "latency_ms" and fields.get("webhook") != "respond":
                continue
            value = _number(fields.get(name))
            if value is not None:
                stats.by_mode[mode][name].append(value)


def _cell(values: Sequence[float], pct: float) -> str:
    result = percentile(values, pct)
    return "-" if result is None else f"{result:.0f}"


def latency_table(stats: LatencyStats) -> str:
    rows = ["| input_mode | field | p50 | p95 | n |", "|---|---|---|---|---|"]
    for mode in sorted(stats.by_mode):
        for name in LATENCY_FIELDS:
            values = stats.by_mode[mode][name]
            if values:
                p50, p95 = _cell(values, 50), _cell(values, 95)
                rows.append(f"| {mode} | {name} | {p50} | {p95} | {len(values)} |")
    if stats.llm_ms:
        rows += ["", "| file | field | p50 | p95 | n |", "|---|---|---|---|---|"]
        for source in sorted(stats.llm_ms):
            values = stats.llm_ms[source]
            rows.append(
                f"| {source} | llm_ms | {_cell(values, 50)} | {_cell(values, 95)} | {len(values)} |"
            )
    if len(rows) == 2:
        rows.append("| (no voice turn lines found) | | | | 0 |")
    return "\n".join(rows)


# --- commands ---------------------------------------------------------------------------


def _latency(files: list[Path]) -> int:
    stats = LatencyStats()
    for path in files:
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                parse_log_lines(handle, stats, source=path.name)
        except OSError:
            print(f"stt_compare: cannot read {path.name}.", file=sys.stderr)
            return REFUSED
    print(latency_table(stats))
    return 0


def _refuse_unsafe() -> str:
    from core.config import settings

    if settings.environment == "production":
        print("stt_compare: refusing to run with ENVIRONMENT=production.", file=sys.stderr)
        raise SystemExit(REFUSED)
    if not (settings.gemini_api_key and settings.gemini_api_key.get_secret_value()):
        print("stt_compare: GEMINI_API_KEY is not set.", file=sys.stderr)
        raise SystemExit(REFUSED)
    return settings.gemini_api_key.get_secret_value()


async def _accuracy(count: int, voice: str, api_key: str) -> int:
    from core.config import settings
    from integrations.stt import GeminiSpeechToText, SttUnavailableError
    from integrations.tts import GeminiTtsClient, TtsUnavailableError
    from services.audio import mulaw_wav_to_pcm16_wav, pcm16_to_mulaw_wav

    tts = GeminiTtsClient(api_key=api_key, model=settings.gemini_tts_model, timeout_seconds=15)
    stt = GeminiSpeechToText(
        api_key=api_key,
        model=settings.gemini_stt_model,
        timeout_seconds=settings.stt_timeout_seconds,
    )
    print(
        f"stt_compare: {count} TTS ({settings.gemini_tts_model}) + {count} STT "
        f"({settings.gemini_stt_model}) requests; these bypass UsageGuard.",
        flush=True,
    )
    rates: list[float] = []
    latencies: list[float] = []
    exact = 0
    done = 0
    for text in UTTERANCES[:count]:
        try:
            speech = await tts.synthesize(text, voice)
            phone = mulaw_wav_to_pcm16_wav(pcm16_to_mulaw_wav(speech.pcm, speech.sample_rate))
            started = time.perf_counter()
            result = await stt.transcribe(phone, "audio/wav")
            stt_ms = (time.perf_counter() - started) * 1000
        except (TtsUnavailableError, SttUnavailableError) as exc:
            if exc.rate_limited:
                print("stt_compare: Gemini answered 429 (quota); stopping.", file=sys.stderr)
            else:
                print(f"stt_compare: {exc}", file=sys.stderr)
            break
        wer = word_error_rate(text, result.text)
        rates.append(wer)
        latencies.append(stt_ms)
        exact += normalize_words(text) == normalize_words(result.text)
        done += 1
        print(
            f"[{done}] wer={wer:.2f} stt_ms={stt_ms:.0f}\n    ref: {text}\n    hyp: {result.text}"
        )
    if not rates:
        return CRASHED
    print(
        f"\nutterances {done}/{count}  mean WER {sum(rates) / len(rates):.3f}  "
        f"exact {exact}/{done}  stt p50 {_cell(latencies, 50)} ms  p95 {_cell(latencies, 95)} ms"
    )
    return 0 if done == count else CRASHED


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stt_compare", description=__doc__.split("\n")[0] if __doc__ else ""
    )
    sub = parser.add_subparsers(dest="command", required=True)
    acc = sub.add_parser("accuracy", help="WER of Gemini STT on phone-quality synthetic speech")
    acc.add_argument("--max-utterances", type=int, default=DEFAULT_UTTERANCES)
    acc.add_argument("--voice", default="Kore")
    lat = sub.add_parser("latency", help="p50/p95 from API log files")
    lat.add_argument("logfiles", nargs="+", type=Path)
    return parser


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="replace")  # pyright: ignore[reportAttributeAccessIssue]
    args = _parser().parse_args(argv)
    if args.command == "latency":
        raise SystemExit(_latency(args.logfiles))
    if not 1 <= args.max_utterances <= MAX_UTTERANCES:
        print(f"stt_compare: --max-utterances must be 1..{MAX_UTTERANCES}.", file=sys.stderr)
        raise SystemExit(REFUSED)
    count = min(args.max_utterances, len(UTTERANCES))
    api_key = _refuse_unsafe()
    try:
        raise SystemExit(asyncio.run(_accuracy(count, args.voice, api_key)))
    except SystemExit:
        raise
    except Exception as exc:
        print(f"stt_compare: crashed ({type(exc).__name__}).", file=sys.stderr)
        raise SystemExit(CRASHED) from None


if __name__ == "__main__":
    main()
