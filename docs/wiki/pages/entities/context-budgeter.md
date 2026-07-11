---
title: Context Budgeter
category: entity
tags: [context, tokens, eviction, resource-management]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/agent/budgeter.py, ROADMAP.md#M4]
status: current
---

# Context Budgeter

Enforces a token ceiling on the context window by tiered, priority-based eviction.

## The Tiers

All five context sources compete for a finite input-token budget. Eviction happens by priority, never silently overflowing the model's window:

**Tier 0 (never drop):**
- System prompt (identity + rules)
- Current message
- Tool definitions
- In-turn observations (tool results fed back during the loop)
- If these alone overflow → `ContextOverflowError` (terminal, non-recoverable)

**Tier 1 (evict last):**
- Factual profile (usually compact, rarely the problem)

**Tier 2 (evict second):**
- Working buffer (evict oldest turns first; they roll into the summary via [[pages/decisions/rolling-summary-rollover]])

**Tier 4 (evict first):**
- Episodic hits (already threshold-gated; drop lowest-scoring first)

**Tier 3 (kept whole if it fits):**
- Summary (rolled-up earlier turns) — re-tightening happens in working memory, not here

## Token Estimation

Uses `estimate_tokens` (a simple char-count heuristic, mirroring llm_kit's default). This is shared with the [[pages/decisions/rolling-summary-rollover]] logic so memory rollover respects the same budget.

## Tuning

Configured via `context.budget_tokens` in `config.yaml`. Typical values: 100K for large models, 25K for smaller ones.

## Rationale

Without the budgeter, context would silently overflow or arbitrarily truncate, degrading model quality unpredictably. By making eviction explicit and tiered, the system is observable: you can see (in logs, metrics) when and what was evicted, then adjust the budget or reduce memory hit counts.
