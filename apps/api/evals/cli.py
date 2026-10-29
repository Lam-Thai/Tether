"""`uv run tether-eval --suite core`: run an eval suite against real Gemini.

    uv run tether-eval --suite core                  # free-tier pacing and budget
    uv run tether-eval --suite core --tier paid      # faster, bigger budget
    uv run tether-eval --suite core --tag faq        # only cases tagged `faq`
    uv run tether-eval --suite core --check          # validate the YAML, no model calls
    uv run tether-eval --purge                       # remove tenants a killed run left

Writes `evals/reports/<UTC timestamp>.json` and `.md` and prints a summary.
Exit code: 0 every target met, 1 a target missed, 2 the run was partial
(budget, rate limit or errors), 3 refused to start, 4 the run crashed.

Refuses to run with `ENVIRONMENT=production` or without `GEMINI_API_KEY`, and,
because it writes a throwaway tenant and real usage counters to the configured
database, refuses a non-local `DATABASE_URL` unless `--allow-remote-db`.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from core.config import settings
from core.db_hosts import is_local_database
from evals.cases import Suite, SuiteError, load_suite
from evals.report import REPORTS_DIR, summary, write
from evals.runner import EvalRunner, RunLimits, Tier, resolve_limits
from evals.scorers import SCORERS

REFUSED = 3
CRASHED = 4


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tether-eval", description="Run an eval suite against real Gemini."
    )
    parser.add_argument("--suite", default="core", help="suite name or directory (default: core)")
    parser.add_argument(
        "--tier",
        choices=("free", "paid"),
        default=None,
        help="Gemini API tier defaults (default: EVAL_TIER, else free)",
    )
    parser.add_argument(
        "--max-requests", type=int, default=None, help="Gemini request budget for the run"
    )
    parser.add_argument("--rpm", type=float, default=None, help="model requests per minute")
    parser.add_argument("--concurrency", type=int, default=None, help="cases played at once")
    parser.add_argument(
        "--tag", action="append", default=[], help="only cases with this tag (repeatable)"
    )
    parser.add_argument("--reports-dir", type=Path, default=REPORTS_DIR)
    parser.add_argument(
        "--check", action="store_true", help="validate the suite and exit without calling the model"
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help="delete eval tenants a killed run left behind, then exit",
    )
    parser.add_argument(
        "--allow-remote-db",
        action="store_true",
        help="allow a DATABASE_URL that is not on this machine",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    # The summary has non-ASCII marks; a redirected Windows stdout defaults to cp1252.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="replace")  # pyright: ignore[reportAttributeAccessIssue]
    args = _parser().parse_args(argv)
    if args.purge:
        _refuse_unsafe_database(args.allow_remote_db)
        raise SystemExit(_purge())

    try:
        suite = load_suite(args.suite, known_scorers=frozenset(SCORERS))
    except SuiteError as exc:
        print(f"tether-eval: {exc}", file=sys.stderr)
        raise SystemExit(REFUSED) from None
    if args.tag:
        wanted = set(args.tag)
        cases = tuple(c for c in suite.cases if wanted & set(c.tags))
        if not cases:
            print(f"tether-eval: no case has tag(s) {sorted(wanted)}.", file=sys.stderr)
            raise SystemExit(REFUSED)
        suite = suite.model_copy(update={"cases": cases})
    tier = args.tier or settings.eval_tier
    try:
        # `is not None`, not `or`: an explicit 0 must be refused, not replaced by a default.
        limits = resolve_limits(
            tier,
            max_requests=_given(args.max_requests, settings.eval_max_requests),
            requests_per_minute=_given(args.rpm, settings.eval_requests_per_minute),
            concurrency=_given(args.concurrency, settings.eval_concurrency),
        )
    except ValueError as exc:
        print(f"tether-eval: {exc}", file=sys.stderr)
        raise SystemExit(REFUSED) from None
    if args.check:
        print(f"suite {suite.name}: {len(suite.cases)} cases OK")
        return

    _refuse_unsafe_database(args.allow_remote_db)
    if not (settings.gemini_api_key and settings.gemini_api_key.get_secret_value()):
        print("tether-eval: GEMINI_API_KEY is not set.", file=sys.stderr)
        raise SystemExit(REFUSED)
    if limits.max_requests > settings.gemini_requests_per_day:
        print(
            f"tether-eval: note: GEMINI_REQUESTS_PER_DAY ({settings.gemini_requests_per_day}) "
            f"is below the run budget ({limits.max_requests}); the global cap may stop it first.",
            file=sys.stderr,
        )
    raise SystemExit(_run(suite, limits, tier, args.reports_dir))


def _refuse_unsafe_database(allow_remote: bool) -> None:
    if settings.environment == "production":
        print("tether-eval: refusing to run with ENVIRONMENT=production.", file=sys.stderr)
        raise SystemExit(REFUSED)
    if not is_local_database(settings.database_url) and not allow_remote:
        print(
            "tether-eval: refusing a DATABASE_URL that is not on this machine; point it at "
            "a local Postgres, or pass --allow-remote-db for a disposable one.",
            file=sys.stderr,
        )
        raise SystemExit(REFUSED)


def _purge() -> int:
    from evals.sandbox import purge_eval_tenants

    removed = asyncio.run(purge_eval_tenants())
    print(f"tether-eval: removed {removed} leftover eval tenant(s).")
    return 0


def _given[T](flag: T | None, setting: T | None) -> T | None:
    return flag if flag is not None else setting


def _run(suite: Suite, limits: RunLimits, tier: Tier, reports_dir: Path) -> int:
    """The real run: Postgres sandbox, real Gemini. Every guard above runs first."""
    # Imported late: these build the database engine and the Gemini client.
    from core.llm import get_llm_client
    from evals.sandbox import postgres_sandbox

    runner = EvalRunner(
        postgres_sandbox, get_llm_client(), limits, model=settings.gemini_model, tier=tier
    )
    print(
        f"tether-eval: {len(suite.cases)} cases, {tier} tier, budget {limits.max_requests} "
        f"requests, {limits.requests_per_minute:g}/min, {limits.concurrency} at a time",
        flush=True,
    )
    try:
        report = asyncio.run(runner.run(suite))
    except Exception as exc:
        # Type only: the message could quote a prompt or a connection string. A crash
        # must not look like a quality result (exit 1), so it gets its own code.
        print(f"tether-eval: the run crashed ({type(exc).__name__}).", file=sys.stderr)
        return CRASHED
    json_path, md_path = write(report, reports_dir)
    print(summary(report))
    print(f"report: {json_path}\n        {md_path}")
    return report.exit_code()
