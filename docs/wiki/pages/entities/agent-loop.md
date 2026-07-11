---
title: Agent Loop
category: entity
tags: [agent, streaming, tool-calling, turn]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/agent/loop.py, CLAUDE.md#Key abstractions to know]
status: current
---

# Agent Loop

The core agentic loop (SPEC §5): context → LLM invoke → tool exec → repeat until done.

## Entry Point

`Agent.run_turn(user_id, conversation_id, user_message)` yields a stream of [[pages/concepts/agent-event-stream]] events.

## Sequence

1. **Context assembly** — Call `ContextBuilder.build()` to fetch and assemble the five sources (system prompt, factual profile, episodic hits, working buffer, current message). See [[pages/entities/context-builder]].

2. **LLM streaming** — Invoke the LLM with the assembled context; stream text chunks as `TextDelta` events. The streaming continues until `StreamEnd` (signaling the model has finished and any tool calls are ready to parse).

3. **Tool execution loop** — For each tool call in `StreamEnd.response.tool_calls`:
   - Emit `ToolCallStarted` event (with parsed arguments, unlike llm_kit's name-only mid-stream event)
   - Execute the tool (with per-tool timeout, rate limiting, permission checks)
   - Emit `ToolResult` event (successful or failed — see [[pages/concepts/tool-errors-as-observations]])
   - Feed the result back to the model's context

4. **Loop check** — If the model made a tool call, go back to step 2 with the updated context (now including the tool result as an observation). Otherwise, emit `TurnComplete` and exit.

## Safety Rails (SPEC §5)

- **`max_iterations` cap** — graceful stop if loop runs too long, never an infinite loop
- **Tool errors are observations** — failed tool becomes `ToolResult(ok=False)` fed to model, never an exception
- **Optional per-turn wall-clock budget** — terminate turn if elapsed time exceeds `agent.per_turn_timeout_s`

## Async & Streaming

The loop is fully async. Streaming is never buffered — each `TextDelta` is yielded immediately (supporting TTFT). Background operations (memory writes, episodic embedding) are enqueued fire-and-forget after `TurnComplete`, so they don't block the response stream.

## Testing

The [[pages/concepts/fake-driven-testing]] approach uses a `FakeLLM` that yields predetermined text chunks and tool calls, so the loop can be tested deterministically without network I/O.
