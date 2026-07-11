---
title: HITL Tool Approval Gates
category: decision
tags: [tools, approval, human-in-loop, safety, gates]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/agent/loop.py, src/harness_kit/serving/app.py, CLAUDE.md#HITL tool approval, ROADMAP.md#M5]
status: current
---

# HITL Tool Approval Gates

Human-in-the-loop approval for sensitive tools. A tool can require explicit user approval before execution.

## Configuration

In `config.yaml`, under a tool's `ToolPolicy`:

```yaml
tools:
  definitions:
    - name: "forget_memory"
      requires_approval: true
      approval_timeout_s: 30  # optional, default 30 seconds
```

The default allowlist in the reference config seeds `forget_memory` with `requires_approval: true` to prevent accidental deletion of episodic memories.

## Event Flow

When a tool requiring approval is called:

1. **Before execution:** [[pages/entities/agent-loop]] emits `ToolApprovalRequired(call_id, tool_name, arguments, timeout_s)` and **awaits** a decision.

2. **On approval:** The loop resumes and executes the tool normally → `ToolCallStarted` → `ToolResult`.

3. **On denial or timeout:** The loop emits `ToolResult(ok=False)` with a human-readable reason ("tool approval denied" or "approval request timed out") and feeds it back to the model as an observation. The model sees the failure and can explain to the user what happened.

## Transport-Specific Behavior

### WebSocket

The WS handler runs two concurrent coroutines:

- `_receive()` — reads incoming messages (including approvals)
- `_run_turns()` — drives the agent loop

Approval responses arrive as:
```json
{"type": "approval", "call_id": "…", "approved": true}
```
or
```json
{"type": "approval", "call_id": "…", "approved": false}
```

`_receive()` routes these to `Agent.resolve_approval(call_id, approved)`, which resolves the `asyncio.Future` the loop is awaiting.

### Server-Sent Events (SSE)

SSE is one-way (server → client). The loop cannot wait for an approval response. Instead:

- The loop yields `ToolApprovalRequired` to the SSE stream.
- Immediately after, the loop's approval future is resolved to `False` (auto-deny).
- A `ToolResult(ok=False)` appears in the stream: "approval request timed out".

Clients wishing to approve must make a separate HTTP request (a future extension; not yet implemented in the reference serving layer).

## Approval Futures & Scaling

Pending approvals live in `Agent._pending_approvals` (in-process memory, keyed by `call_id`). In a multi-worker deployment, the approval response must reach the same worker as the running turn. WS connections are typically sticky, so this is safe in practice. A shared-store (Redis) backing for approval state is a later scaling optimization.

## Timeout Behavior

If no approval/denial arrives within `approval_timeout_s`:

- **WS:** the loop's future times out and is treated as a denial.
- **SSE:** the timeout happens synchronously (auto-deny, as above).

Both result in `ToolResult(ok=False)` being sent to the model.

## Integration with Permission & Rate-Limiting

The full execution order is:

1. [[pages/concepts/permission-model|Permission check]]
2. [[pages/decisions/rate-limiting-in-process|Rate-limit check]]
3. **Approval gate** (this page)
4. Execution

A tool can have all three gates; they are applied in this order.

## Example: forget_memory

The reference allowlist includes `forget_memory` (for erasing past conversations) with `requires_approval: true`. This prevents the model from accidentally calling it without explicit user consent. When the model invokes `forget_memory`, the user sees the `ToolApprovalRequired` event, decides whether to confirm, and the result (success or denial) is fed back to the model as an observation.
