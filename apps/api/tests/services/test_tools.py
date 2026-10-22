"""`ToolRegistry` + `ToolExecutor` and the `search_faq` tool, at their boundary. No DB, no model."""

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from core.tenant import Tenant
from models.business import Business
from models.user import User
from schemas.agent_turn import ToolCall
from services.faq_search import SEARCH_FAQ, search_faq_tool
from services.knowledge import KnowledgeBase
from services.tools import (
    MAX_CALLS_PER_ROUND,
    MAX_LOGGED_STRING_CHARS,
    Tool,
    ToolContext,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
)
from tests.services.memory_faq_store import MemoryFaqStore, MemoryOperationLog


def _tenant(name: str = "Alpha Salon") -> Tenant:
    user = User(id=uuid.uuid4(), external_auth_id="user_x", email="owner@example.com")
    business = Business(
        id=uuid.uuid4(),
        owner_id=user.id,
        name=name,
        industry="Salon",
        address="1 Main St",
        timezone="America/New_York",
    )
    return Tenant(user=user, business=business)


def _business_id(tenant: Tenant) -> uuid.UUID:
    assert tenant.business is not None
    return tenant.business.id


class LookupArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
        Field(description="What to look up."),
    ]
    size: Literal["small", "large"] = "small"
    count: int | None = None


class Spy:
    def __init__(self, *, fail: bool = False) -> None:
        self.seen: list[tuple[ToolContext, LookupArgs]] = []
        self.fail = fail

    async def __call__(self, ctx: ToolContext, args: LookupArgs) -> ToolResult:
        self.seen.append((ctx, args))
        if self.fail:
            raise RuntimeError(f"db exploded while handling {args.code}")
        return ToolResult(for_model={"found": 1, "size": args.size}, for_log={"count": 1})


def _lookup(spy: Spy) -> Tool[LookupArgs]:
    return Tool(name="lookup", description="Look a code up.", args=LookupArgs, run=spy)


def _setup(spy: Spy | None = None):
    spy = spy or Spy()
    log = MemoryOperationLog()
    tenant = _tenant()
    ctx = ToolContext(tenant=tenant, interaction_id=uuid.uuid4())
    executor = ToolExecutor(ToolRegistry.of([_lookup(spy)]), log)
    return spy, log, ctx, executor


# --- Registry ----------------------------------------------------------------


def test_declaration_is_derived_from_the_args_model():
    (declaration,) = ToolRegistry.of([_lookup(Spy())]).declarations()

    assert declaration.name == "lookup"
    assert declaration.description == "Look a code up."
    params = declaration.parameters
    assert params["type"] == "object"
    assert params["required"] == ["code"]
    assert params["properties"]["code"] == {
        "type": "string",
        "description": "What to look up.",
        "minLength": 1,
        "maxLength": 500,
    }
    assert params["properties"]["size"] == {"type": "string", "enum": ["small", "large"]}
    assert params["properties"]["count"] == {"type": "integer"}  # Optional flattened
    assert "title" not in str(params)


def test_registry_lookups_and_duplicate_names():
    registry = ToolRegistry.of([_lookup(Spy())])
    assert registry.has("lookup") and not registry.has("book")
    assert ToolRegistry().declarations() == ()
    with pytest.raises(ValueError):
        ToolRegistry.of([_lookup(Spy()), _lookup(Spy())])


def test_nested_argument_models_are_refused():
    class Inner(BaseModel):
        x: int

    class Outer(BaseModel):
        inner: Inner

    async def run(ctx, args):  # pragma: no cover - never called
        raise AssertionError

    with pytest.raises(ValueError):
        Tool(name="nested", description="d", args=Outer, run=run).declaration()


# --- Executor ----------------------------------------------------------------


async def test_valid_call_runs_and_logs_a_succeeded_row_with_a_verified_result():
    spy, log, ctx, executor = _setup()

    outcome = await executor.execute(
        ctx, ToolCall(name="lookup", arguments={"code": "  A1  ", "size": "large"})
    )

    assert outcome.status == "succeeded"
    assert outcome.for_model == {"found": 1, "size": "large"}
    ((seen_ctx, args),) = spy.seen
    assert seen_ctx is ctx and args.code == "A1"  # validated, stripped
    (row,) = log.rows
    assert row["business_id"] == _business_id(ctx.tenant)
    assert row["interaction_id"] == ctx.interaction_id
    assert row["tool_name"] == "lookup" and row["status"] == "succeeded"
    assert row["arguments"] == {"code": "A1", "size": "large", "count": None}
    assert row["result"] == {"count": 1}
    assert isinstance(row["duration_ms"], int) and row["duration_ms"] >= 0


async def test_invalid_args_are_rejected_logged_by_name_only_and_never_executed():
    spy, log, ctx, executor = _setup()
    secret = "CUSTOMER-SECRET-VALUE"

    outcome = await executor.execute(
        ctx,
        ToolCall(name="lookup", arguments={"code": "", "size": secret, "evil<<<x>>>": secret}),
    )

    assert outcome.status == "rejected"
    assert spy.seen == []
    assert outcome.for_model == {
        "error": "invalid_arguments",
        "fields": ["code", "size"],
        "unexpected_arguments": 1,
    }
    (row,) = log.rows
    assert row["status"] == "rejected"
    assert row["arguments"] == {"argument_names": ["code", "evil<<<x>>>", "size"]}
    assert row["result"] == {
        "reason": "invalid_arguments",
        "fields": ["code", "size"],
        "unexpected_arguments": 1,
    }
    assert secret not in str(log.rows) and secret not in str(outcome)
    # A model-chosen argument name never reaches the model-facing result.
    assert "evil" not in str(outcome.for_model)


async def test_missing_required_arg_is_rejected():
    spy, log, ctx, executor = _setup()
    outcome = await executor.execute(ctx, ToolCall(name="lookup", arguments={}))
    assert outcome.status == "rejected" and outcome.for_model["fields"] == ["code"]
    assert spy.seen == [] and log.rows[0]["arguments"] == {"argument_names": []}


async def test_null_arguments_count_as_not_given():
    # Gemini's schema makes every argument nullable (the union across tools).
    spy, log, ctx, executor = _setup()
    outcome = await executor.execute(
        ctx, ToolCall(name="lookup", arguments={"code": "A1", "size": None, "other_tool": None})
    )
    assert outcome.status == "succeeded"
    assert spy.seen[0][1].size == "small"


async def test_wrong_type_is_rejected_not_coerced():
    spy, _, ctx, executor = _setup()
    outcome = await executor.execute(ctx, ToolCall(name="lookup", arguments={"code": 123}))
    assert outcome.status == "rejected" and spy.seen == []


async def test_executor_exception_is_failed_with_a_fixed_reason_and_no_text_logged(caplog):
    spy, log, ctx, executor = _setup(Spy(fail=True))
    secret = "CUSTOMER-SECRET-VALUE"

    with caplog.at_level(logging.DEBUG):
        outcome = await executor.execute(ctx, ToolCall(name="lookup", arguments={"code": secret}))

    assert outcome.status == "failed"
    assert outcome.for_model == {"error": "tool_unavailable"}
    (row,) = log.rows
    assert row["status"] == "failed" and row["result"] == {"reason": "executor_error"}
    assert secret not in caplog.text and "exploded" not in caplog.text
    assert any(getattr(r, "tool", None) == "lookup" for r in caplog.records)


async def test_the_executor_runs_isolated_and_a_failure_is_logged_after_rollback():
    events: list[str] = []

    @asynccontextmanager
    async def savepoint():
        events.append("open")
        try:
            yield
        except BaseException:
            events.append("rolled_back")
            raise
        events.append("released")

    class Log(MemoryOperationLog):
        async def record_operation(self, **row):
            events.append(f"logged:{row['status']}")
            await super().record_operation(**row)

    ctx = ToolContext(tenant=_tenant(), interaction_id=uuid.uuid4())
    for spy, expected in (
        (Spy(), ["open", "released", "logged:succeeded"]),
        (Spy(fail=True), ["open", "rolled_back", "logged:failed"]),
    ):
        events.clear()
        executor = ToolExecutor(ToolRegistry.of([_lookup(spy)]), Log(), isolate=savepoint)
        await executor.execute(ctx, ToolCall(name="lookup", arguments={"code": "A1"}))
        assert events == expected


async def test_unknown_tool_is_never_executed_and_is_logged_as_rejected():
    spy, log, ctx, executor = _setup()
    outcome = await executor.execute(ctx, ToolCall(name="book", arguments={"when": "now"}))
    assert outcome.status == "rejected" and outcome.for_model == {"error": "unknown_tool"}
    assert spy.seen == []
    assert log.rows[0]["result"] == {"reason": "unknown_tool"}
    assert log.rows[0]["arguments"] == {"argument_names": ["when"]}


async def test_long_strings_are_truncated_in_the_log():
    spy, log, ctx, executor = _setup()
    await executor.execute(ctx, ToolCall(name="lookup", arguments={"code": "x" * 500}))
    assert spy.seen[0][1].code == "x" * 500  # the executor gets the full validated value
    assert len(log.rows[0]["arguments"]["code"]) == MAX_LOGGED_STRING_CHARS


async def test_a_round_executes_at_most_the_cap_and_rejects_the_rest():
    spy, log, ctx, executor = _setup()
    calls = [ToolCall(name="lookup", arguments={"code": f"c{i}"}) for i in range(5)]

    outcomes = await executor.run_round(ctx, calls)

    assert [o.status for o in outcomes] == ["succeeded"] * MAX_CALLS_PER_ROUND + ["rejected"] * 2
    assert len(spy.seen) == MAX_CALLS_PER_ROUND
    assert outcomes[-1].for_model == {"error": "too_many_calls"}
    assert [r["status"] for r in log.rows] == [o.status for o in outcomes]
    assert log.rows[-1]["result"] == {"reason": "too_many_calls"}


# --- search_faq ----------------------------------------------------------------


def _faq_setup(*, many: bool = False):
    faqs = MemoryFaqStore()
    tenant = _tenant()
    business = _business_id(tenant)
    walk_in = faqs.add(business, "Do you take walk-ins?", "Yes, before 3 PM.", "Booking")
    disabled = faqs.add(business, "Do you sell gift cards?", "Yes, any amount.", active=False)
    if many:
        for i in range(8):
            faqs.add(business, f"Unrelated question {i}?", f"Unrelated answer {i}.")
    other_tenant = _tenant("Beta Barbers")
    faqs.add(_business_id(other_tenant), "Do you take walk-ins?", "No, never.")
    log = MemoryOperationLog()
    tool = search_faq_tool(KnowledgeBase(faqs))
    executor = ToolExecutor(ToolRegistry.of([tool]), log)
    ctx = ToolContext(tenant=tenant, interaction_id=uuid.uuid4())
    return executor, log, ctx, walk_in, disabled


def test_search_faq_declaration():
    (declaration,) = ToolRegistry.of(
        [search_faq_tool(KnowledgeBase(MemoryFaqStore()))]
    ).declarations()
    assert declaration.name == SEARCH_FAQ == "search_faq"
    assert declaration.parameters["required"] == ["query"]
    assert declaration.parameters["properties"]["query"]["maxLength"] == 200


async def test_search_faq_returns_only_this_businesss_active_faqs():
    executor, log, ctx, walk_in, disabled = _faq_setup()

    outcome = await executor.execute(
        ctx, ToolCall(name="search_faq", arguments={"query": " walk in? "})
    )

    assert outcome.status == "succeeded"
    assert [f.id for f in outcome.faqs] == [walk_in.id]
    assert outcome.faqs[0].answer == "Yes, before 3 PM."
    assert outcome.for_model == {"matches": 1, "faq_ids": [str(walk_in.id)]}
    (row,) = log.rows
    assert row["arguments"] == {"query": "walk in?"}
    assert row["result"] == {"faq_ids": [str(walk_in.id)], "count": 1}
    assert str(disabled.id) not in str(outcome) + str(log.rows)
    assert "No, never." not in str(outcome)


async def test_search_faq_says_so_explicitly_when_nothing_matches():
    executor, log, ctx, *_ = _faq_setup(many=True)

    outcome = await executor.execute(
        ctx, ToolCall(name="search_faq", arguments={"query": "parking validation"})
    )

    assert outcome.status == "succeeded" and outcome.faqs == ()
    assert outcome.for_model == {"matches": 0, "result": "no matching FAQs"}
    assert log.rows[0]["result"] == {"faq_ids": [], "count": 0}


@pytest.mark.parametrize("query", ["", "   ", "x" * 201, None])
async def test_search_faq_rejects_blank_or_overlong_queries(query):
    executor, log, ctx, *_ = _faq_setup()
    outcome = await executor.execute(ctx, ToolCall(name="search_faq", arguments={"query": query}))
    assert outcome.status == "rejected" and outcome.faqs == ()
    assert log.rows[0]["result"]["fields"] == ["query"]
