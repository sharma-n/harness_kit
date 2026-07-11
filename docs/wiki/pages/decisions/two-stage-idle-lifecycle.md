---
title: Two-Stage Idle Lifecycle
category: decision
tags: [memory, session-management, ttl, finalization]
created: 2026-07-11
updated: 2026-07-11
sources: [CLAUDE.md#Memory design decisions, src/harness_kit/agent/loop.py]
status: current
---

# Two-Stage Idle Lifecycle

## Decision

Conversation end is not a single TTL. Instead, two timers: `idle_finalize_s` (embed but keep resumable) then `ttl_s` (evict the session).

## Mechanism

When a conversation goes idle (no activity for `idle_finalize_s`):

1. **Finalize stage:** The [[pages/entities/agent-loop]] calls `Agent.end_conversation()`, which:
   - Calls [[pages/entities/episodic-memory]] to embed the conversation and optional flagged moments
   - Sets `SessionState.finalized_at` (timestamp)
   - **Does NOT evict the session** from the store — the session remains loadable
   
2. **Resume:** If the user returns and sends a message before `ttl_s` expires:
   - The [[pages/entities/working-memory]] loads the session normally (it still exists)
   - `finalized_at` is cleared, so the conversation is "un-finalized"
   - The loop continues as if the idle pause never happened
   
3. **TTL stage (later):** After `ttl_s` from creation (config validates `idle_finalize_s < ttl_s`):
   - The session is evicted from [[pages/entities/stores-overview#sessionstore]] **only**
   - Embeddings in the [[pages/entities/episodic-memory]] are **never deleted** — they remain in VectorStore indefinitely (it is write-only; no TTL enforcement)

## Benefits

- **Seamless resume:** User can step away and return to the same conversation without interruption. No loss of working buffer.
- **Embedding is available:** Episodic hits can retrieve this conversation for future conversations (so the model can reference prior work).
- **Operational simplicity:** Two timeouts are simpler than complex cleanup logic. You can set a short `idle_finalize_s` (5 min) to embed quickly, then a long `ttl_s` (30 days) to keep sessions resumable for a while.

## Driving Mechanisms

`end_conversation` is called from two places:

1. **WebSocket disconnect** (fast path): When the client disconnects, `serving/app.py` calls `end_conversation` immediately.
2. **Background idle sweeper** (`Agent.sweep_idle`): Runs on a cadence (e.g., every 60 sec), finds sessions idle past `idle_finalize_s`, and finalizes them. **This is what gives SSE (one-way transport with no disconnect signal) a conversation-end event**, and it also catches abrupt WS drops.

Idempotent: calling `end_conversation` when already finalized (or session expired) is a no-op.

## Config Validation

`HarnessKitConfig` validates `idle_finalize_s < ttl_s` at load time. Misconfiguration is caught early.

## Status

✅ Implemented in Batch M6 (ROADMAP).
