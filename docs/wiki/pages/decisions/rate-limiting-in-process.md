---
title: Rate-Limiting: In-Process, Per-(User, Tool)
category: decision
tags: [tools, rate-limiting, scaling, performance, buckets]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/tools/ratelimit.py, CLAUDE.md#HITL tool approval, ROADMAP.md#M10]
status: current
---

# Rate-Limiting: In-Process, Per-(User, Tool)

## Decision

Each tool can have a per-user rate limit (tokens per minute). The `ToolRateLimiter` enforces this with in-process token buckets, rejecting (not queueing) calls that exceed the limit.

## Algorithm

A refilling token bucket, per `(user_id, tool_name)` pair. Same refill math as `llm_kit`'s own `TokenBucket` (monotonic clock, no async lock). Key differences for a tool gate:

- **Non-blocking reject:** `llm_kit` waits until tokens are available (right for rate-limiting LLM requests). A tool gate **rejects immediately**, so rate-limited calls become `ToolResult(ok=False)` observations fed back to the model (never a stall, since time-to-first-token is harness_kit's identity).

- **Per-(user, tool):** Buckets keyed by `(user_id, tool_name)` so one high-value tool can be rate-limited per user without affecting others (e.g., API-call tools might be per-minute limited; memory tools unlimited).

## Configuration

In `config.yaml`, under a tool's `ToolPolicy`:

```yaml
tools:
  definitions:
    - name: "expensive_api_tool"
      rate_limit_per_minute: 10
```

Omit to disable (unlimited). Per-tool defaults to no limit; rate-limiting is opt-in per tool.

## In-Process Caveat

Buckets live in process memory. In a multi-worker deployment, each worker maintains its own buckets — the effective ceiling is roughly `workers × rate_limit_per_minute`. This is the same tradeoff `llm_kit`'s own limiter makes (identical code path). A shared-store (Redis) backing is a later scaling optimization, not needed for the reference implementation.

## Bounded Memory (v1.7)

Buckets are stored in a `BoundedLRUDict` capped at 1000 entries by default. Oldest (least-recently-used) buckets are evicted when the cap is exceeded. This prevents unbounded growth in deployments with high cardinality of users or tools.

## Error Feedback

When a tool call is rate-limited, `ToolRegistry.execute()` returns:
```python
ToolResult(ok=False, content="rate limit exceeded for tool 'X' (10 calls per minute)")
```

This is fed back to the model as an observation, allowing the model to explain the delay to the user or retry the call in a future turn.

## Integration with Permissions & Approval

Rate-limiting happens *after* [[pages/concepts/permission-model|permission checks]] but *before* [[pages/decisions/hitl-approval-gates|approval gates]]. The order is: permission → rate-limit → approval → execute.
