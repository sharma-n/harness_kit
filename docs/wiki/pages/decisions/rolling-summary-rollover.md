---
title: Rolling-Summary Rollover Trigger
category: decision
tags: [memory, token-budget, rollover, design-tradeoff]
created: 2026-07-11
updated: 2026-07-11
sources: [CLAUDE.md#Memory design decisions, src/harness_kit/memory/working.py]
status: current
---

# Rolling-Summary Rollover Trigger

## Decision

Trigger rollover when the [[pages/entities/working-memory]] buffer exceeds `buffer_token_budget`, **not** a fixed turn count.

## Rationale

- **Respects context window:** Token-driven eviction ensures the buffer never silently overflow or exceed the model's limit, regardless of turn size.
- **Scales with conversation:** Long turns and short turns are handled uniformly. A buffer with 10 long turns might exceed budget while a buffer with 50 short turns stays within it.
- **Predictable:** You can reason about buffer behavior in terms of tokens, the same unit as the model's input limit and the [[pages/entities/context-budgeter]].

## Mechanism

When `working.load()` detects buffer token count > `buffer_token_budget`:

1. Select the oldest turns (in order) until they fit within ~20% of the budget (some hysteresis to avoid thrashing).
2. Pass them to the LLM with a `RolledSummary` response model: "Summarize these turns in 1–3 sentences of key facts and decisions."
3. Fold the summary into the summary block (tier 3 in [[pages/entities/context-budgeter]]).
4. Drop the original turns from the buffer.

The newest turns within budget remain (verbatim) so the current conversation context stays rich.

## Off-Hot-Path

Rollover is enqueued after `TurnComplete`, not on the critical path. If the LLM fails to summarize or returns unusable text, it's a safe no-op — the buffer is not corrupted, and the oldest turns remain for the next attempt.

## Token Estimation

Uses the same `estimate_tokens` (char-count heuristic) as the [[pages/entities/context-budgeter]], shared via `harness_kit.tokens`. This ensures memory rollover respects the same token assumptions as context assembly.

## Alternative Considered

**Fixed turn count:** E.g., "evict every 20 turns." Simpler but fragile — a 20-turn conversation could be 50 KB or 1 MB depending on turn length. Fixed-count doesn't scale.

## Status

✅ Implemented and tested in Batch M6 (ROADMAP).
