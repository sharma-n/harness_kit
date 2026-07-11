---
title: Factual Memory
category: entity
tags: [memory, facts, profile, extraction]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/memory/factual.py, CLAUDE.md#Memory design decisions]
status: current
---

# Factual Memory

Stores durable facts (attributes, preferences, history) about users in a profile store, extracted from conversations.

## Extraction

After each turn, `FactualMemory.extract(user_id, conversation_id, turns)` is enqueued fire-and-forget. The LLM identifies new facts (e.g., "user is a data scientist", "prefers async patterns") and durable context (not ephemeral discussion) — appends them to the user's profile.

**Token-driven:** Uses the same `estimate_tokens` as [[pages/decisions/rolling-summary-rollover]] so extraction respects the model's context limits.

**Off the hot path:** Extraction runs after `TurnComplete`, not blocking the response stream.

## Profile

The user profile is fetched by the [[pages/entities/context-builder]] and injected into the system message (tier-0, never evicted). It typically reads:

```
User profile:
- Senior software engineer (10 years)
- Interested in Go, Rust, Python
- Prefers async-first patterns
- Works on distributed systems
```

## User Scoping

Profile is per `user_id`. Extraction is scoped so facts are tagged with the correct user. No cross-user leakage.

## Distinction from Episodic

- **Factual:** stable, high-confidence, user attributes, can be acted on directly (e.g., "show Go examples")
- **Episodic:** discussion context, topics explored, situations in past conversations (e.g., "they worked on a rate-limiter in conversation #42")

See [[pages/synthesis/memory-system-overview]] for how the three memory modules interlock.

## Idempotency

Fact extraction and appending are idempotent (if the same fact is extracted again, it's not duplicated). Off-hot-path retries are safe; store-write failures are logged and retried via `retry.store_write`.
