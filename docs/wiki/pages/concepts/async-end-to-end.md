---
title: Async End-to-End
category: concept
tags: [async, concurrency, performance, latency]
created: 2026-07-11
updated: 2026-07-11
sources: [CLAUDE.md#Async end-to-end]
status: current
---

# Async End-to-End

**Everything is `async`.** A synchronous DB or network call on the event loop stalls every concurrent conversation — treat it as a bug.

## The Principle

In a long-lived service handling multiple concurrent conversations, a single blocking operation blocks the entire event loop. The [[pages/entities/agent-loop]] is multiplexed across many conversations; if one turn's memory read stalls the event loop, other users' turns are delayed. This violates harness_kit's core identity around **latency**.

## Coverage

- **In-memory stores** (`memory_session.py`, `memory_profile.py`, `memory_vectors.py`) are async, not `sync`.
- **Agent loop** (`agent/loop.py`, `agent/context.py`) is fully async.
- **Memory operations** (working buffer, episodic retrieval, factual extraction) are async.
- **Tool execution** is async; tool timeouts are per-tool and enforced via `asyncio.wait_for`.
- **Serving transports** (`serving/app.py`) use FastAPI's async handlers.

## Adapter Strategy

The real store backends (Redis, SQLite, Qdrant) all use async clients:
- **Redis:** `redis.asyncio`
- **SQLite:** `aiosqlite`
- **Qdrant:** `AsyncQdrantClient`

Because the store Protocols are async at the top, dropping in a real adapter is just swapping the implementation — existing code sees no change. See [[pages/decisions/real-store-backends]] for details.

## Testing

The test harness (`tests/conftest.py`) uses `asyncio.run()` and the [[pages/entities/agent-loop]] can be driven by test fixtures that are themselves async.
