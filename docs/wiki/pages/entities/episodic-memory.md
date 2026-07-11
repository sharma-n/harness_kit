---
title: Episodic Memory
category: entity
tags: [memory, vector-search, embeddings, conversation-end]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/memory/episodic.py, CLAUDE.md#Memory design decisions]
status: current
---

# Episodic Memory

Stores conversation summaries as vector embeddings in the [[pages/entities/stores-overview#vectorstore]], searchable by semantic similarity.

## Embedding Strategy

**Per-conversation, not per-turn** — At conversation end, the entire conversation (rolling summary + working buffer) is embedded as ONE vector point with `kind="conversation"`. The point ID is deterministic (`conv:{conversation_id}`), so re-finalizing a resumed conversation upserts rather than duplicates.

**Trade-off:** cheaper and more compact (one embedding per conversation) vs. per-turn (higher granularity but N embeddings per conversation). Current design prioritizes cost and scalability.

## Flagged Moments

When `flagged_moments_enabled` is true (default false), the LLM identifies 1–`max_flagged_moments` notable discussion threads at conversation end. Each is embedded as a sibling point (`kind="moment"`, deterministic ID `moment:{conversation_id}:{i}`). These compete naturally with the conversation point in `top_k` search — no Protocol change needed. Balances broad recall (conversation point) with topic precision (moment points).

## Two-Stage Idle Lifecycle

See [[pages/decisions/two-stage-idle-lifecycle]]: conversation is embedded at `idle_finalize_s` but session stays resumable; session evicted at `ttl_s`. Embeddings are write-only in VectorStore — no TTL or deletion enforced there. Re-finalizing a resumed conversation upserts (idempotent).

## Runtime Age-Decay

When `decay_rate > 0` (default 0.05, halves score after ~14 days), search multiplies each hit's score by `exp(-rate * age_days)`. Fetches `top_k * 2` candidates before decay to re-rank and re-cap. No writes — always current.

## User Scoping

All retrieval is `user_id`-filtered. Deletion (`forget_memory` tool) verifies user ownership via `list_points`.

## Integration

The [[pages/entities/context-builder]] calls `EpisodicMemory.retrieve(user_id, user_message, buffer)` to find relevant past conversations (augmented query: user message + current buffer context). Top-K hits feed into the [[pages/entities/context-budgeter]] as tier-4 (evicted first if token budget is tight).
