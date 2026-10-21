---
name: gemini-tool
description: Add a tool Gemini can call (e.g. search_faq, check_availability, create_booking). Use when the agent needs a new capability exposed to the model, or an existing tool's arguments or behavior change.
---

# Gemini tool

Gemini proposes a tool call; it never executes anything. This skill covers the full loop from declaration to a verified result.

## Steps

1. **Declaration schema.** Write the tool's name, description, and argument schema as Gemini sees it — concise, since it's part of every prompt. Keep the description action-oriented (what it does), not implementation detail.
2. **Pydantic arg validation.** Validate Gemini's arguments against a Pydantic schema on the backend before touching anything. Invalid arguments are a validation failure, not a retry-the-prompt situation (see the AI failure handling rule in the root `CLAUDE.md` / PRD §9.4).
3. **Executor.** The function FastAPI runs to actually perform the check or action (query the DB, call the calendar, etc.), scoped to the resolved `business_id`. A provider call inside it is metered like any other (`provider-adapter` skill); a reached limit returns a verified "unavailable" result to the model, never a guessed answer. It returns a *verified* result — the real outcome, not an echo of what Gemini asked for. If the tool writes anything (`create_booking`), it re-validates right before writing (e.g. recheck calendar availability) rather than trusting Gemini's earlier read.
4. **Operation log entry.** Every tool execution that changes state is recorded before the result goes back to Gemini.
5. **Fake-LLM tests.** Test the executor directly, and test the full loop with a fake Gemini client that returns a scripted tool call — never a live Gemini call in tests.
6. **Eval cases.** Add synthetic conversation cases exercising this tool to the eval harness, including at least one case where the model should *not* call it (ambiguous request, missing required info).

## Checklist

- [ ] Argument schema validated server-side, independent of what Gemini claims.
- [ ] Executor scoped to `business_id`; writes re-validate before committing.
- [ ] Result returned to Gemini is the verified outcome, not a pass-through of the request.
- [ ] State-changing calls logged to the operation log.
- [ ] Fake-LLM test + eval cases added.
