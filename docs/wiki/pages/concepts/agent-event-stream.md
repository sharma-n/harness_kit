---
title: Agent Event Stream
category: concept
tags: [streaming, events, abstraction, loop]
created: 2026-07-11
updated: 2026-07-11
sources: [CLAUDE.md#Key abstractions to know, src/harness_kit/agent/events.py, src/harness_kit/serving/wire.py]
status: current
---

# Agent Event Stream

The **`AgentEvent`** type (defined in `src/harness_kit/agent/events.py`) is the load-bearing abstraction for streaming the [[pages/entities/agent-loop]] to clients.

## Event Types

A turn yields one or more events in sequence:

- **`TextDelta`** — a chunk of streamed text, emitted during LLM generation
- **`ToolCallStarted`** — a tool call is about to execute (includes name and parsed arguments)
- **`ToolApprovalRequired`** — a tool requires human approval (see [[pages/decisions/hitl-approval-gates]]); loop pauses here
- **`ToolResult`** — the outcome of a tool call, whether successful or failed (see [[pages/concepts/tool-errors-as-observations]])
- **`TurnComplete`** — the turn has finished; includes metadata (iteration count, final state)

## Why Events, Not Just Text?

A streaming chatbot could just emit raw text tokens, but that loses critical context:
- Clients can't know **when** a tool is being called or **what** happened
- The serving layer can't emit granular state changes (e.g., "tool approved" vs. "tool denied")
- The [[pages/entities/context-budgeter]] and memory system need turn-end events to fire background operations

## Serving Integration

`serving/wire.py` encodes each `AgentEvent` to a JSON frame for transmission over WebSocket or SSE. This allows clients to update UI in lockstep with agent progress (e.g., show a "tool is running" spinner on `ToolCallStarted`, dismiss it on `ToolResult`).

## Testing

Events are deterministic and enumerable, making it easy to test the agent loop without a real LLM — the loop can be driven by a `FakeLLM` that yields a predetermined sequence of events.
