---
title: Background Writes — Fire-and-Forget with Retry
category: concept
tags: [async, background-tasks, reliability, error-handling]
created: 2026-07-11
updated: 2026-07-11
sources: [CLAUDE.md#Memory design decisions, src/harness_kit/agent/loop.py]
status: current
---

# Background Writes — Fire-and-Forget with Retry

Memory operations ([[pages/decisions/rolling-summary-rollover]], [[pages/entities/episodic-memory]] finalization, [[pages/entities/factual-memory]] extraction) are **not** on the critical path. They are enqueued off the hot path via `Agent._enqueue()` and run as background tasks.

## Why Fire-and-Forget?

The [[pages/entities/agent-loop]] must not be blocked by slow I/O. Embedding a conversation or extracting facts could take seconds. The client is waiting for the response stream to end, so memory operations must complete asynchronously, out of band.

## Implementation

Background tasks run in `Agent._bg_tasks` (a `set[asyncio.Task]`). The loop enqueues them but continues immediately. Each task is wrapped in `_guard()`:

- **Logs errors:** If a background write fails, one ERROR log is emitted with operation + `user_id` + `conversation_id` (no silent failures).
- **Retries store writes:** Via `retry.store_write()` (exp backoff + jitter), which wraps **only** the store call, not the LLM/embedder step before it. The LLM/embedder already have their own retries (via llm_kit), so re-running them would be wasteful.
- **Propagates OTel context:** Each background task runs under the same trace as the turn, so memory operations are visible in Langfuse (see [[pages/entities/telemetry]]).

## All Operations Are Idempotent

Every background write is verified idempotent:
- `maybe_rollover`: summarizes and evicts the buffer (same result every time)
- `mark_finalized`: sets `finalized_at` to a timestamp (idempotent)
- `write_conversation`: upserts via deterministic point ID `conv:{conversation_id}` (idempotent)
- `extract`: appends facts to profile (verified safe if duplicate facts appear)

Except: `append_turn` (append-only to the working buffer) — but this runs synchronously on the hot path, not fire-and-forget.

## Failure Handling

A failure in a background write is:
- Logged (operator sees it)
- Retried (transient faults are recovered)
- **Not cascaded** — loop continues, other background tasks run, conversation is not aborted

The [[pages/decisions/two-stage-idle-lifecycle]] sweeper also logs per-conversation WARNINGs and continues; batch failures don't cause the sweeper to stop.

## Tuning

Retry behavior is controlled via `memory.store_retry` in `config.yaml` — backoff strategy, max attempts, jitter.
