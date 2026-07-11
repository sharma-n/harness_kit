---
title: Multi-User Scoping
category: concept
tags: [multi-user, isolation, security, foundational]
created: 2026-07-11
updated: 2026-07-11
sources: [CLAUDE.md#Multi-user is foundational (not a later feature)]
status: current
---

# Multi-User Scoping

Multi-user isolation is **foundational**, not a later-stage feature. It is threaded through every layer of the stack.

## Three Pillars

**Sessions are user-owned.** `SessionStore.load(conversation_id, user_id)` raises `UnauthorizedError` if the conversation belongs to a different user. A user can never load another user's session.

**Memory is user-scoped.** Profile is per `user_id`; episodic search (see [[pages/entities/episodic-memory]]) always filters by `user_id`. No cross-user leakage, ever. Factual memories are tagged with `user_id` at write time.

**Tool permissions are per-user.** [[pages/concepts/permission-model]] resolves each user's allowed tool set. The registry filters tool definitions by user AND re-checks at execution time (defense in depth). The global `config.yaml` only sets the default allowlist; per-user grants live in the store.

## Scaling Implication

When adding anything that touches user data, ask: **is it scoped to `user_id`?**

If a new store or cache holds per-user state in process memory *without* a shared-store backing, it breaks horizontal scaling (documented in SPEC §12). **Exception:** the M10 tool rate limiter (`tools/ratelimit.py`) deliberately uses in-process per-user token buckets, mirroring `llm_kit`'s own approach — so multi-worker deploys enforce roughly `workers × rate_limit_per_minute`. A Redis backing is deferred as a later scaling step.

## Verification

Every store Protocol (`SessionStore`, `ProfileStore`, `VectorStore`, `PermissionStore`, `SkillStore`) includes user-scoped contract tests verifying that cross-user access is blocked and search results are filtered by user.
