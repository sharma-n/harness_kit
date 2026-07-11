---
title: Tool Errors as Observations
category: concept
tags: [tools, error-handling, loop, model-feedback]
created: 2026-07-11
updated: 2026-07-11
sources: [CLAUDE.md#Key abstractions to know]
status: current
---

# Tool Errors as Observations

A critical inversion of traditional error handling: **tool errors are not exceptions. They are observations fed back to the model.**

## The Pattern

When a tool call fails (denied, timed out, rate-limited, or errored), the [[pages/entities/agent-loop]] does not raise an exception. Instead, it emits a `ToolResult(ok=False)` event with a human-readable reason and feeds that observation back to the model's context.

The model then:
- Understands what went wrong
- Decides how to proceed (retry, explain to the user, try a different tool, etc.)
- Continues the turn normally

## Contrast

Traditional approaches:
- Throw an exception → loop catches it → loop returns error to user → conversation is over
- Hard to recover from transient failures
- Model has no agency in error handling

Harness Kit approach:
- Tool fails → becomes an observation → model reasons about it → loop continues normally
- Transient failures (rate limit, timeout) can be retried
- Model explains the error to the user or adapts its strategy
- Conversation continues naturally

## Implementation

In `src/harness_kit/tools/registry.py`, the `execute()` method catches exceptions from tool calls and returns a `ToolResult` with `ok=False`. The loop receives this as a normal event and feeds the reason to the model.

See [[pages/decisions/hitl-approval-gates]] for an example: when a tool requires approval and the user denies it, that denial is *not* an exception — it's a `ToolResult(ok=False, reason="User denied approval")` fed back to the model.

## Only Two Exceptions

The only conditions that raise (terminating the loop immediately) are:
- `max_iterations` exceeded (graceful stop)
- `UnauthorizedError` — user is trying to access another user's data

Everything else is an observation.
