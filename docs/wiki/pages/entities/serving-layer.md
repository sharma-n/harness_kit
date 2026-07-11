---
title: Serving Layer
category: entity
tags: [serving, fastapi, websocket, sse, transport, streaming]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/serving/{app,wire}.py, CLAUDE.md#Per-conversation model switching, ROADMAP.md#Serving]
status: current
---

# Serving Layer

FastAPI-based transport layer for streaming [[pages/entities/agent-loop|agent events]] to clients over two protocols.

## Transports

**WebSocket `/ws/{conversation_id}`** — Bidirectional. Client sends:
```json
{"user_id": "alice", "message": "hello"}
```
Server streams encoded [[pages/concepts/agent-event-stream|AgentEvent]] frames as they arrive (TextDelta, ToolCallStarted, ToolResult, TurnComplete, etc.). Can also send approval responses:
```json
{"type": "approval", "call_id": "…", "approved": true}
```
and model-switching commands:
```json
{"type": "set_model", "user_id": "alice", "model": "claude-opus-4-8"}
```

**SSE `/sse/{conversation_id}`** — One turn per HTTP POST request. `user_id` and `message` as query params. Server streams encoded event frames until turn completes. One-way (no approval responses or model switching mid-turn, though REST endpoints provide those separately).

## Backpressure

Each send is awaited, so a slow client paces its own stream without stalling the shared event loop. Other concurrent connections run as independent async tasks.

## Concurrent Connections

Each connection runs `_receive()` and `_run_turns()` concurrently:

- `_receive()` — reads all incoming messages (text turns, approvals, model-switch commands)
- `_run_turns()` — drives the agent loop from an internal queue

The pattern allows the client to send new turns or approvals while the loop is still processing (e.g., approving a tool mid-turn).

## Idle Sweeper

Background task (`_idle_sweep_loop`) runs continuously during the app's lifetime. Finalizes conversations that exceed `idle_finalize_s` without activity. This is the transport-agnostic conversation-end signal — SSE (which has no disconnect event) and abruptly-dropped WebSocket connections rely on it to emit [[pages/decisions/two-stage-idle-lifecycle|idle finalization]].

## Lifespan

FastAPI lifespan handler:

1. **Startup:** Call `service.astart()` to connect MCP servers and warm up resources. Start idle sweeper task.
2. **Shutdown:** Cancel sweeper, drain background writes, close MCP connections and shared HTTP client via `service.aclose()`.

## Auth Stub

`user_id` comes from the client (untrusted). A real deployment must resolve it from a verified token (JWT, OAuth, etc.). The rest of the stack treats `user_id` as the isolation key, so only this resolver needs replacement.

## Endpoints

- `GET /healthz` — Health check
- `GET /metrics` — Prometheus metrics (if enabled; 501 if disabled)
- `GET /conversations?user_id=alice&status=active|finalized` — List user's conversations with metadata (including model override)
- `PUT /conversations/{conversation_id}/model?user_id=alice&model=claude-opus-4-8` — Set per-conversation model override
- `WebSocket /ws/{conversation_id}` — Bidirectional streaming
- `POST /sse/{conversation_id}?user_id=alice` — One-turn SSE (message in request body)

## Event Encoding

Raw `AgentEvent` objects are encoded to JSON frames by `serving/wire.py` before streaming. Each frame has a `type` (e.g., "text_delta", "tool_call_started", "tool_result") and payload.

## Integration with Agent Loop

The serving layer is decoupled from the agent loop via the [[pages/entities/agent-loop|AgentEvent]] abstraction. Changes to serving don't affect the loop; changes to the loop don't affect transport-level details.
