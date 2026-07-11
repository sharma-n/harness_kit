---
title: Memory System Overview
category: synthesis
tags: [memory, architecture, working-episodic-factual]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/memory/working.py, src/harness_kit/memory/episodic.py, src/harness_kit/memory/factual.py, CLAUDE.md#Memory design decisions]
status: current
---

# Memory System Overview

Harness Kit implements a three-part memory system: working (this conversation), episodic (past conversations), and factual (user attributes).

## Three Layers

### [[pages/entities/working-memory]]

**Scope:** This conversation.
**Storage:** Session-scoped buffer (appended after each turn).
**Retrieval:** Loaded at turn start, passed to [[pages/entities/context-builder]].
**Eviction:** When buffer exceeds `buffer_token_budget`, oldest turns roll into summary ([[pages/decisions/rolling-summary-rollover]]) and are dropped.

### [[pages/entities/episodic-memory]]

**Scope:** Past conversations.
**Storage:** Vector embeddings in VectorStore, searchable by semantic similarity.
**Embedding:** At conversation end, the whole conversation (summary + buffer) → one point. Optional flagged moments are siblings.
**Retrieval:** Augmented query (user message + buffer context) finds top-K relevant past conversations.
**Eviction:** Never from VectorStore (write-only, no TTL). Sessions are evicted after `ttl_s`.

**Trade-off:** [[pages/decisions/episodic-embedding-granularity]] — per-conversation embedding is cheaper than per-turn but sacrifices per-turn precision. Flagged moments refine this balance.

### [[pages/entities/factual-memory]]

**Scope:** User attributes (stable facts).
**Storage:** Profile stored in ProfileStore, injected into system message (tier-0, never evicted).
**Extraction:** After each turn, LLM identifies new facts and appends them to the profile (off hot path).
**Retrieval:** Loaded at turn start, merged into the system message.

## Integration

1. **Turn start:** 
   - Load [[pages/entities/working-memory]] (this conversation)
   - Load [[pages/entities/factual-memory]] (user profile)
   - Search [[pages/entities/episodic-memory]] (past conversations)
   - Assemble into context via [[pages/entities/context-builder]]

2. **Turn end:**
   - Append turn to working buffer (sync)
   - Enqueue [[pages/decisions/rolling-summary-rollover]] (if buffer exceeds budget)
   - Enqueue [[pages/entities/factual-memory]] extraction
   - (Later, on idle) Enqueue [[pages/entities/episodic-memory]] write

## Fire-and-Forget Model

See [[pages/concepts/background-writes-fire-and-forget]]: memory writes are off the hot path, enqueued after `TurnComplete`, logged and retried if they fail.

## Two-Stage Idle

See [[pages/decisions/two-stage-idle-lifecycle]]: conversation is finalized (embedded) at `idle_finalize_s`, session is evicted at `ttl_s`. Embeddings persist indefinitely (available for future episodic recall).

## Distinction: Facts vs. Context

- **Factual:** stable attributes ("user is a data scientist") → used to ground model reasoning
- **Episodic:** discussion context ("they worked on a rate-limiter in conversation #42") → used to provide relevant precedent
- **Working:** current turn + recent context → used for immediate flow

All three feed the [[pages/entities/context-builder]] in a priority-ordered system (system prompt with factual first, then episodic, then working buffer).
