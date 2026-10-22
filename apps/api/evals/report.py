"""`EvalReport`: what one run measured, rendered as JSON, Markdown and a console summary.

Metrics count only scored cases; a case that never ran (budget or rate limit
reached first) or broke on an error is listed but excluded, and the report
says the run is partial. Every failing case is written out with its full
transcript (customer line, reply, engine and model intent, cited sources,
tools run), so a prompt regression can be debugged from the report alone.
Reports hold synthetic utterances and model replies only, never a prompt or
a key.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from evals.cases import EvalCase, Suite
from evals.playback import Playback, PlayedTurn
from evals.scorers import Score

REPORTS_DIR = Path(__file__).parent / "reports"

CaseStatus = Literal["scored", "not_run", "error"]
# Why a run stopped before every case was played.
StopReason = Literal["budget_exhausted", "quota_exhausted", "rate_limited", "errors"]

_STOP_MESSAGES: dict[str, str] = {
    "budget_exhausted": "the request budget or a usage limit was reached",
    "quota_exhausted": "the provider's quota is used up (a daily quota, by its retry delay)",
    "rate_limited": "the provider kept rate-limiting after the allowed backoffs",
    "errors": "too many cases failed with errors in a row",
}


@dataclass(frozen=True)
class CaseResult:
    case: EvalCase
    status: CaseStatus
    playback: Playback | None = None
    scores: tuple[Score, ...] = ()
    # Exception type only, never its message (it could quote prompt or customer text).
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "scored" and all(score.passed for score in self.scores)


@dataclass(frozen=True)
class MetricResult:
    scorer: str
    label: str
    target: float | None
    passed: int
    total: int

    @property
    def rate(self) -> float | None:
        return self.passed / self.total if self.total else None

    @property
    def met(self) -> bool | None:
        """None when there is no target or nothing was scored."""
        if self.target is None or self.rate is None:
            return None
        return self.rate >= self.target


@dataclass(frozen=True)
class EvalReport:
    suite: str
    model: str
    tier: str
    max_requests: int
    started_at: datetime
    finished_at: datetime
    stopped: StopReason | None
    # The provider's retry delay when a quota stopped the run.
    retry_after_seconds: float | None
    model_calls: int
    tokens_in: int
    tokens_out: int
    metrics: tuple[MetricResult, ...]
    cases: tuple[CaseResult, ...]

    @property
    def complete(self) -> bool:
        return self.stopped is None and all(c.status == "scored" for c in self.cases)

    @property
    def targets_met(self) -> bool:
        return all(metric.met is not False for metric in self.metrics)

    def exit_code(self) -> int:
        """0: complete and every target met; 1: a target missed; 2: partial run."""
        if not self.complete:
            return 2
        return 0 if self.targets_met else 1


def build_metrics(suite: Suite, results: tuple[CaseResult, ...]) -> tuple[MetricResult, ...]:
    metrics: list[MetricResult] = []
    for metric in suite.metrics:
        scores = [
            score
            for result in results
            if result.status == "scored"
            for score in result.scores
            if score.scorer == metric.scorer
        ]
        metrics.append(
            MetricResult(
                scorer=metric.scorer,
                label=metric.label,
                target=metric.target,
                passed=sum(score.passed for score in scores),
                total=len(scores),
            )
        )
    return tuple(metrics)


def _turn_dict(turn: PlayedTurn) -> dict[str, Any]:
    return {
        "customer": turn.customer,
        "reply": turn.reply,
        "intent": turn.intent,
        "model_intent": turn.model_intent,
        "sources": list(turn.sources),
        "fallback": turn.fallback,
        "operations": [
            {"tool": op.tool_name, "status": op.status, "result": dict(op.result)}
            for op in turn.operations
        ],
        "model_calls": turn.model_calls,
        "rate_limited": turn.rate_limited,
    }


def to_dict(report: EvalReport) -> dict[str, Any]:
    return {
        "suite": report.suite,
        "model": report.model,
        "tier": report.tier,
        "max_requests": report.max_requests,
        "started_at": report.started_at.isoformat(),
        "finished_at": report.finished_at.isoformat(),
        "complete": report.complete,
        "stopped": report.stopped,
        "retry_after_seconds": report.retry_after_seconds,
        "model_calls": report.model_calls,
        "tokens": {"in": report.tokens_in, "out": report.tokens_out},
        "metrics": [
            {
                "scorer": m.scorer,
                "label": m.label,
                "passed": m.passed,
                "total": m.total,
                "rate": m.rate,
                "target": m.target,
                "met": m.met,
            }
            for m in report.metrics
        ],
        "cases": [
            {
                "id": r.case.id,
                "tags": list(r.case.tags),
                "status": r.status,
                "passed": r.passed,
                "expected_intent": r.case.expected_intent,
                "scores": [
                    {"scorer": s.scorer, "passed": s.passed, "detail": s.detail} for s in r.scores
                ],
                "error": r.error,
                "transcript": [_turn_dict(t) for t in r.playback.turns] if r.playback else [],
            }
            for r in report.cases
        ],
    }


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _mark(met: bool | None) -> str:
    return {True: "✅", False: "❌", None: "–"}[met]


def summary(report: EvalReport) -> str:
    """The console summary: one line per metric, then what stopped a partial run."""
    scored = sum(1 for c in report.cases if c.status == "scored")
    lines = [
        f"suite {report.suite} · {report.model} · {report.tier} tier · "
        f"{scored}/{len(report.cases)} cases scored · {report.model_calls} model calls",
    ]
    width = max(len(m.label) for m in report.metrics)
    for metric in report.metrics:
        target = f"(target ≥ {metric.target * 100:.0f}%)" if metric.target is not None else ""
        lines.append(
            f"{metric.label:<{width}}   {_percent(metric.rate):>6} {target:<17}  "
            f"{_mark(metric.met)}  [{metric.passed}/{metric.total}]"
        )
    if report.stopped:
        lines.append(f"PARTIAL: stopped early because {_stop_message(report)}.")
    errors = sum(1 for c in report.cases if c.status == "error")
    if errors:
        lines.append(f"PARTIAL: {errors} case(s) failed with an error.")
    return "\n".join(lines)


def _stop_message(report: EvalReport) -> str:
    message = _STOP_MESSAGES[report.stopped] if report.stopped else ""
    if report.retry_after_seconds:
        message += f"; the provider says retry in {report.retry_after_seconds / 3600:.1f}h"
    return message


def _one_line(text: str) -> str:
    return " ".join(text.split())


def to_markdown(report: EvalReport) -> str:
    out = [
        f"# Eval report: `{report.suite}`",
        "",
        f"- Model: `{report.model}` ({report.tier} tier, budget {report.max_requests} requests)",
        f"- Started {report.started_at.isoformat()}, finished {report.finished_at.isoformat()}",
        f"- Model calls: {report.model_calls} · tokens in/out: "
        f"{report.tokens_in}/{report.tokens_out}",
        f"- Status: {'complete' if report.complete else 'PARTIAL'}"
        + (f" ({_stop_message(report)})" if report.stopped else ""),
        "",
        "| Metric | Result | Target | Met |",
        "| --- | --- | --- | --- |",
    ]
    for m in report.metrics:
        target = f"≥ {m.target * 100:.0f}%" if m.target is not None else "–"
        out.append(
            f"| {m.label} | {_percent(m.rate)} ({m.passed}/{m.total}) | {target} | {_mark(m.met)} |"
        )

    failed = [r for r in report.cases if r.status == "scored" and not r.passed]
    out += ["", f"## Failures ({len(failed)})", ""]
    if not failed:
        out.append("None.")
    for result in failed:
        problems = "; ".join(f"`{s.scorer}`: {s.detail}" for s in result.scores if not s.passed)
        out += [f"### `{result.case.id}`", "", f"- {problems}"]
        if result.case.tags:
            out.append(f"- tags: {', '.join(result.case.tags)}")
        if result.case.expected_intent:
            out.append(f"- expected intent: {result.case.expected_intent}")
        out.append("")
        for number, turn in enumerate(result.playback.turns if result.playback else (), 1):
            tools = ", ".join(f"{op.tool_name} ({op.status})" for op in turn.operations) or "none"
            meta = (
                f"intent {turn.intent}, model {turn.model_intent or 'none'}, "
                f"fallback {turn.fallback or 'no'}, sources {list(turn.sources) or 'none'}, "
                f"tools {tools}"
            )
            out += [
                f"{number}. **Customer:** {_one_line(turn.customer)}",
                f"   **Agent:** {_one_line(turn.reply)}",
                f"   _{meta}_",
            ]
        out.append("")

    skipped = [r for r in report.cases if r.status != "scored"]
    if skipped:
        out += [f"## Not scored ({len(skipped)})", ""]
        out += [
            f"- `{r.case.id}`: {r.status}" + (f" ({r.error})" if r.error else "") for r in skipped
        ]
        out.append("")
    return "\n".join(out)


def write(report: EvalReport, directory: Path = REPORTS_DIR) -> tuple[Path, Path]:
    """`<directory>/<UTC timestamp>.json` and `.md`. Returns both paths."""
    directory.mkdir(parents=True, exist_ok=True)
    stem = report.started_at.strftime("%Y-%m-%dT%H%M%SZ")
    json_path, md_path = directory / f"{stem}.json", directory / f"{stem}.md"
    json_path.write_text(json.dumps(to_dict(report), indent=2) + "\n", encoding="utf-8")
    md_path.write_text(to_markdown(report), encoding="utf-8")
    return json_path, md_path
