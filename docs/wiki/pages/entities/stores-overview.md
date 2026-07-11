---
title: Stores Overview
category: entity
tags: [stores, protocols, backends, multi-user]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/stores/base.py, ROADMAP.md#Real store backends]
status: current
---

# Stores Overview

Five Protocol abstractions for persistent storage, all user-scoped.

## Protocols

**SessionStore** — User-owned conversation sessions (working buffer + state). `load(conv_id, user_id)` raises `UnauthorizedError` for wrong user. Provides `list(user_id)` for metadata listing.

**ProfileStore** — User attributes and extracted facts. Per-`user_id`.

**PermissionStore** — Tool permissions (what each user can execute). Defaults to a global allowlist; per-user overrides in the store.

**VectorStore** — Episodic embeddings. `retrieve(user_id, query, top_k)` returns only hits belonging to `user_id`. Supports `delete` and `list_points` for [[pages/entities/jobs-offline]].

**SkillStore** — Skill visibility grants. `allowed_skills(user_id)` → `None` (all visible) or `set[str]` (restricted).

## In-Memory Adapters

All Protocols have in-memory reference implementations (`memory_*.py`). Used in testing; enable deterministic tests without external infra.

## Real Backends (ROADMAP M8)

- **Redis SessionStore:** JSON at `session:{conv_id}` with `EXPIRE`; indexes for listing and idle sweeper
- **SQLite ProfileStore + PermissionStore:** via `aiosqlite` (swappable to Postgres)
- **Qdrant VectorStore:** async client; in-memory, file, or remote modes; always `user_id`-filtered via `FieldCondition`

## Async Throughout

Every Protocol method is `async`. Adapters use async clients (redis.asyncio, aiosqlite, AsyncQdrantClient) so no event-loop blocking.

See [[pages/decisions/real-store-backends]] for backend rationale and implementation details.
