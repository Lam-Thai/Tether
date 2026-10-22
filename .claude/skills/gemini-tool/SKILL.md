---
name: gemini-tool
description: Add a tool Gemini can call (e.g. search_faq, check_availability, create_booking). Use when the agent needs a new capability exposed to the model, or an existing tool's arguments or behavior change.
---

# Gemini tool

Gemini proposes a tool call; it never executes anything. Tool requests travel inside the JSON reply (`AgentTurn.tool_calls`), not Gemini's native function calling, because Gemini 2.5 can't combine structured output with it. `services/conversation.py` runs the bounded loop (`MAX_TOOL_ROUNDS = 3`, at most `tools.MAX_CALLS_PER_ROUND` calls each), and `services/tools.ToolExecutor` is the only thing that executes a call. `services/faq_search.py` (`search_faq`) is the reference tool to copy.

## Steps

1. **Args model.** A flat Pydantic model with `extra="forbid"` and a `Field(description=...)` on each argument. `Tool.declaration()` derives the JSON schema the model sees from it, so it can't drift from what the backend enforces. Nested models and unions other than `X | None` are refused. Every argument reaches Gemini as nullable, and a null means "not given".
2. **Executor.** `async def run(ctx: ToolContext, args) -> ToolResult`. Scope every read and write by `ctx.tenant`, never by an id the model sent. Return the *verified* outcome: `for_model` (short and JSON-able; what the model sees in TOOL RESULTS), `for_log` (ids, counts and reason codes only, never free text), and `evidence` (what the reply may then rely on: FAQ snippets, services, hours; `services/grounding.py` accepts only ids and amounts that arrived as evidence this turn). Raise `ToolRejection(reason, fields=...)` for arguments only the executor can judge (a date out of range): the call is logged `rejected` and its savepoint rolled back. Read business facts only through `BusinessDirectory`, never the tables. A provider call inside it is metered (`provider-adapter` skill); a reached limit returns a verified "unavailable" result, never a guess. A writing tool (`create_booking`) re-validates right before it writes.
3. **Register it.** Expose a factory (`my_tool(deps) -> Tool[Args]`) and add it to the `ToolRegistry.of([...])` list in `core/conversation.get_conversation_engine`, built from repositories bound to the request session. That is the only change outside the new module: the engine, the prompt and the Gemini schema pick it up from the registry.
4. **Operation log.** `ToolExecutor` already writes one `interaction_operations` row per call, whether it succeeded, was rejected (bad args: never executed, only argument names logged) or failed (the executor raised: the model sees `tool_unavailable`, only the tool name is logged). Each executor runs inside a savepoint (`store.savepoint()`), so a DB error rolls back only that tool's work. The engine checkpoints before the next model call, so the row is durable before the model hears about it. Don't log or return exception messages; they can quote customer text.
5. **Grounding.** If the tool returns facts the customer may be told, put them in `ToolResult.evidence` and check that `ACCEPTED_SOURCES` / `GROUNDED_INTENTS` in `services/grounding.py` cover the intents that state them. Prices and durations are also checked as numbers in the reply text, so a tool returning a new kind of amount must feed the amount check.
6. **Prompt rules.** If the tool can make the model claim something happened (a booking, an SMS), check `PLATFORM_RULES` / `platform_rules()` in `services/prompt_builder.py` still forbid claiming it without a TOOL RESULTS confirmation. Two tools declaring the same argument name with different types are refused by `response_schema_for_gemini`.
7. **Tests.** Test the executor directly (`tests/services/test_tools.py` style) and the full loop with `FakeLLMClient` scripting `agent_turn(tool_calls=[...])` then the final reply, using `tests/services/memory_conversation_store.py` / `memory_faq_store.py`. Never a live call in the suite. Cover: a verified result reaching the next request, invalid args rejected without executing, cross-tenant scoping, and the model *not* calling the tool when it shouldn't.
8. **Live smoke case.** There's no eval harness yet: add an opt-in `-m live` case to `tests/integrations/test_gemini_live.py` over synthetic seed data (`seeds/`), including one case where the tool must not be used or must come back empty.

## Checklist

- [ ] Args model is flat, `extra="forbid"`, with field descriptions; validated server-side whatever Gemini claims.
- [ ] Executor scoped by `ctx.tenant`; writes re-validate before committing.
- [ ] `ToolResult` is the verified outcome, and `for_log` holds no free text.
- [ ] Registered in `core/conversation.py`; no engine change needed.
- [ ] Fake-LLM loop test + live smoke case added.
