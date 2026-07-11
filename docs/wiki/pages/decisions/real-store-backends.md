---
title: Real Store Backends
category: decision
tags: [stores, persistence, scaling, backends]
created: 2026-07-11
updated: 2026-07-11
sources: [ROADMAP.md#Real store backends, src/harness_kit/stores/]
status: current
---

# Real Store Backends

## Decision

Ship in-memory adapters as reference; provide real backends (Redis, SQLite, Qdrant) for production.

## Backends

**Redis SessionStore** — Conversation sessions as JSON blobs with idle-reset `EXPIRE`. Side-indexes (ZSETs) for listing (`user:{uid}:convs`) and idle sweeper (`sessions:pending_finalize`). Avoids full-key SCAN performance cliff.

**SQLite ProfileStore + PermissionStore** — `aiosqlite` + SQLAlchemy Core. Postgres-compatible (just swap the connection string). Default permission stored as `user_id='__default__'` sentinel row.

**Qdrant VectorStore** — `AsyncQdrantClient` supporting three modes: `memory` (in-process, no Docker), `file` (local persistence), `host` (remote). Always `user_id`-filtered via `FieldCondition`. String point IDs mapped to UUIDs stored in payload. Supports `delete` and `list_points` for the M8 offline jobs.

## User Scoping

All backends verify user ownership at the adapter level (before returning data or executing deletes). The caller is never trusted to pass only their own IDs.

## Async Clients

- `redis.asyncio` (async Redis)
- `aiosqlite` (async SQLite)
- `AsyncQdrantClient` (Qdrant's async API)

No blocking I/O on the event loop.

## Testing

Contract tests in `tests/test_stores_real.py` verify all backends against the Protocol. SQLite + Qdrant always run (no external services needed). Redis tests skip if port 6379 is unreachable.

## Status

✅ All three backends implemented and tested in ROADMAP.
