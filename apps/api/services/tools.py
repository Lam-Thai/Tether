"""`ToolRegistry` + `ToolExecutor`: the backend operations the model may request,
and the one place that runs them.

Gemini proposes, the backend verifies. A `Tool` is a name, a description, a
Pydantic args model (`extra="forbid"`) and an async executor. Its
`ToolDeclaration` (the JSON-schema the model sees) is derived from the args
model, so the schema the model is steered with and the one the backend
enforces can't drift apart. Adding a tool is one registration; the engine
doesn't change.

`ToolExecutor.execute` is the safety boundary:

- An unregistered name is never executed. (The engine already treats one as
  invalid model output; this is the backstop.)
- Arguments are validated against the args model first. A miss is `rejected`
  and the executor is never called.
- An executor that raises is `failed`; the model is told only that the tool
  is unavailable, and only the tool name is logged. Each executor runs inside
  `isolate()` (a savepoint, from the store), so one that fails on a DB error
  rolls back only its own work and the failure can still be logged.
- Otherwise `succeeded`, with the executor's verified result: what the backend
  actually found, never an echo of what the model asked for.
- Every call, whatever its status, is written to the operation log before its
  result goes back to the model. Logged arguments are the validated ones,
  truncated; a rejected call logs only the argument names it was sent.
  Logged results are machine summaries (ids, counts, a reason code).

Executors scope everything by `ToolContext.tenant`, never by an id the model
supplied. No FastAPI or SQLAlchemy imports: the operation log is a protocol
the conversation store implements.
"""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ValidationError

from core.tenant import Tenant
from integrations.llm.port import ToolDeclaration
from schemas.agent_turn import ToolCall
from services.agent import require_business
from services.knowledge import FaqSnippet

logger = logging.getLogger("tether.tools")

OperationStatus = Literal["succeeded", "rejected", "failed"]
# Opens a unit of work an executor's failure can be rolled back to (a savepoint).
Isolate = Callable[[], AbstractAsyncContextManager[Any]]

# Calls executed per model reply; any beyond this are rejected (and logged), not run.
MAX_CALLS_PER_ROUND = 3
MAX_LOGGED_STRING_CHARS = 200
MAX_LOGGED_ITEMS = 20
MAX_LOGGED_NAME_CHARS = 64

# JSON-schema keywords a tool's argument schema may use: the subset both the
# backend validates and Gemini's response schema can express.
_SCHEMA_KEYWORDS = (
    "type",
    "description",
    "enum",
    "format",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "items",
)


@dataclass(frozen=True)
class ToolContext:
    """What an executor may rely on. Scope every read and write by `tenant`."""

    tenant: Tenant
    interaction_id: uuid.UUID


@dataclass(frozen=True)
class ToolResult:
    """An executor's verified outcome."""

    # Shown to the model in TOOL RESULTS: JSON-able, backend-verified, short.
    for_model: Mapping[str, Any]
    # Stored in the operation log: ids, counts, reason codes. Never free text.
    for_log: Mapping[str, Any]
    # FAQs this call retrieved: rendered as FAQ RESULTS, and the only ids the
    # grounding guard accepts as `sources` this turn.
    faqs: tuple[FaqSnippet, ...] = ()


@dataclass(frozen=True)
class ToolOutcome:
    """What happened to one requested call, as the engine and the prompt see it."""

    name: str
    status: OperationStatus
    for_model: Mapping[str, Any]
    faqs: tuple[FaqSnippet, ...] = ()


@dataclass(frozen=True)
class Tool[ArgsT: BaseModel]:
    name: str
    # Action-oriented; it is part of every prompt that declares the tool.
    description: str
    # Must set `extra="forbid"` and use flat, JSON-schema-friendly fields.
    args: type[ArgsT]
    run: Callable[[ToolContext, ArgsT], Awaitable[ToolResult]]

    def declaration(self) -> ToolDeclaration:
        return ToolDeclaration(
            name=self.name, description=self.description, parameters=_parameters(self.args)
        )


class OperationLog(Protocol):
    async def record_operation(
        self,
        *,
        business_id: uuid.UUID,
        interaction_id: uuid.UUID,
        tool_name: str,
        arguments: Mapping[str, Any],
        status: OperationStatus,
        result: Mapping[str, Any],
        duration_ms: int,
    ) -> None:
        """Append one row to `interaction_operations` (not committed by itself)."""
        ...


@dataclass(frozen=True)
class ToolRegistry:
    _tools: dict[str, Tool[Any]] = field(default_factory=dict)

    @classmethod
    def of(cls, tools: Iterable[Tool[Any]] = ()) -> "ToolRegistry":
        registered: dict[str, Tool[Any]] = {}
        for tool in tools:
            if tool.name in registered:
                raise ValueError(f"Tool registered twice: {tool.name}")
            registered[tool.name] = tool
        return cls(registered)

    def declarations(self) -> tuple[ToolDeclaration, ...]:
        return tuple(tool.declaration() for tool in self._tools.values())

    def has(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> Tool[Any] | None:
        return self._tools.get(name)


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        log: OperationLog,
        *,
        max_calls_per_round: int = MAX_CALLS_PER_ROUND,
        isolate: Isolate = nullcontext,
    ) -> None:
        self._registry = registry
        self._log = log
        self._max_calls = max_calls_per_round
        self._isolate = isolate

    async def run_round(self, ctx: ToolContext, calls: Sequence[ToolCall]) -> list[ToolOutcome]:
        """Runs one reply's calls in order (sequentially: they may share a DB session).
        Calls past the per-round cap are rejected and logged, never executed."""
        outcomes: list[ToolOutcome] = []
        for index, call in enumerate(calls):
            if index < self._max_calls:
                outcomes.append(await self.execute(ctx, call))
            else:
                outcomes.append(await self._reject(ctx, call, {"reason": "too_many_calls"}))
        return outcomes

    async def execute(self, ctx: ToolContext, call: ToolCall) -> ToolOutcome:
        tool = self._registry.get(call.name)
        if tool is None:
            return await self._reject(ctx, call, {"reason": "unknown_tool"})

        started = time.perf_counter()
        # Gemini's schema makes every argument nullable (one shape for all tools),
        # so null means "not given" and the args model's defaults apply.
        given = {name: value for name, value in call.arguments.items() if value is not None}
        try:
            args = tool.args.model_validate(given)
        except ValidationError as exc:
            return await self._reject(ctx, call, _invalid(exc, tool.args), started=started)

        logged_args = _sanitize(args.model_dump(mode="json"))
        try:
            async with self._isolate():
                result = await tool.run(ctx, args)
        except Exception as exc:
            # Never the exception message: it may quote arguments or customer text.
            logger.warning(
                "tool execution failed",
                extra={
                    "tool": tool.name,
                    "interaction_id": str(ctx.interaction_id),
                    "error_type": type(exc).__name__,
                },
            )
            await self._record(
                ctx, tool.name, logged_args, "failed", {"reason": "executor_error"}, started
            )
            return ToolOutcome(tool.name, "failed", {"error": "tool_unavailable"})

        await self._record(
            ctx, tool.name, logged_args, "succeeded", _sanitize(result.for_log), started
        )
        return ToolOutcome(tool.name, "succeeded", dict(result.for_model), tuple(result.faqs))

    async def _reject(
        self,
        ctx: ToolContext,
        call: ToolCall,
        reason: Mapping[str, Any],
        *,
        started: float | None = None,
    ) -> ToolOutcome:
        logger.info(
            "tool call rejected",
            extra={
                "tool": call.name if self._registry.has(call.name) else "unregistered",
                "interaction_id": str(ctx.interaction_id),
                "reason": reason["reason"],
            },
        )
        names = sorted(_truncate(str(name), MAX_LOGGED_NAME_CHARS) for name in call.arguments)
        await self._record(
            ctx,
            call.name,
            {"argument_names": names[:MAX_LOGGED_ITEMS]},
            "rejected",
            reason,
            started if started is not None else time.perf_counter(),
        )
        for_model = {
            "error": reason["reason"],
            **{k: v for k, v in reason.items() if k != "reason"},
        }
        return ToolOutcome(call.name, "rejected", for_model)

    async def _record(
        self,
        ctx: ToolContext,
        tool_name: str,
        arguments: Mapping[str, Any],
        status: OperationStatus,
        result: Mapping[str, Any],
        started: float,
    ) -> None:
        await self._log.record_operation(
            business_id=require_business(ctx.tenant).id,
            interaction_id=ctx.interaction_id,
            tool_name=tool_name,
            arguments=arguments,
            status=status,
            result=result,
            duration_ms=max(0, round((time.perf_counter() - started) * 1000)),
        )


def _invalid(exc: ValidationError, model: type[BaseModel]) -> dict[str, Any]:
    """Which declared fields failed, plus how many undeclared names were sent. Never
    a value, and never an undeclared (model-chosen) name: this reaches the prompt."""
    fields: set[str] = set()
    unexpected = 0
    for error in exc.errors():
        if error["type"] == "extra_forbidden":
            unexpected += 1
        elif error["loc"] and str(error["loc"][0]) in model.model_fields:
            fields.add(str(error["loc"][0]))
    reason: dict[str, Any] = {"reason": "invalid_arguments", "fields": sorted(fields)}
    if unexpected:
        reason["unexpected_arguments"] = unexpected
    return reason


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def _sanitize(value: Any) -> Any:
    """JSON-able, bounded copy for the operation log: strings truncated, collections capped."""
    if isinstance(value, str):
        return _truncate(value, MAX_LOGGED_STRING_CHARS)
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, Mapping):
        items = list(value.items())[:MAX_LOGGED_ITEMS]
        return {_truncate(str(k), MAX_LOGGED_NAME_CHARS): _sanitize(v) for k, v in items}
    if isinstance(value, list | tuple | set | frozenset):
        return [_sanitize(v) for v in list(value)[:MAX_LOGGED_ITEMS]]
    return _truncate(str(value), MAX_LOGGED_STRING_CHARS)


def _parameters(model: type[BaseModel]) -> dict[str, Any]:
    """The args model as a small JSON-schema object (no titles, no `$defs`)."""
    schema = model.model_json_schema()
    if "$defs" in schema:
        raise ValueError(f"{model.__name__}: tool arguments must be flat (no nested models).")
    properties = {
        name: _property(name, prop) for name, prop in schema.get("properties", {}).items()
    }
    parameters: dict[str, Any] = {"type": "object", "properties": properties}
    if schema.get("required"):
        parameters["required"] = list(schema["required"])
    return parameters


def _property(name: str, prop: Mapping[str, Any]) -> dict[str, Any]:
    if "anyOf" in prop:  # `X | None`: null already means "not given"
        options = [option for option in prop["anyOf"] if option.get("type") != "null"]
        if len(options) != 1:
            raise ValueError(f"Tool argument {name!r}: unions are not supported.")
        prop = {**options[0], **{k: v for k, v in prop.items() if k != "anyOf"}}
    subset = {key: prop[key] for key in _SCHEMA_KEYWORDS if key in prop}
    if "type" not in subset:
        raise ValueError(f"Tool argument {name!r} needs a plain JSON type.")
    if "items" in subset:
        subset["items"] = _property(name, subset["items"])
    return subset
