---
title: Episodic Embedding Granularity
category: decision
tags: [memory, embeddings, cost-scaling, tradeoff]
created: 2026-07-11
updated: 2026-07-11
sources: [CLAUDE.md#Memory design decisions, src/harness_kit/memory/episodic.py]
status: current
---

# Episodic Embedding Granularity

## Decision

Embed **per-conversation, not per-turn** — at conversation end, the entire conversation (rolling summary + buffer) becomes one vector point in the [[pages/entities/episodic-memory]] store.

## Trade-Off

**Pros (per-conversation):**
- Cost: 1 embedding per conversation vs. N embeddings per conversation
- Scalability: VectorStore point count is O(conversations), not O(turns)
- Simpler semantics: a conversation is a semantic unit (a coherent discussion thread)

**Cons (per-conversation):**
- Recall precision: searching for a specific topic within a 50-turn conversation returns the whole conversation, not the specific turn where the topic was discussed
- May include irrelevant turns: the vector is an average of the whole conversation, losing per-turn nuance

## Refinement: Flagged Moments

When `flagged_moments_enabled` is true, the LLM identifies 1–`max_flagged_moments` notable discussion threads within the conversation. Each thread is embedded as a sibling `kind="moment"` point. These compete with the conversation point in `top_k` search.

**Effect:** Two-layer balance:
- Conversation point: broad "what was this conversation about?" recall
- Moment points: precise recall for specific topics within the conversation

This retains most recall precision of per-turn embedding without the cost.

## Point ID Strategy

The per-conversation point ID is deterministic: `conv:{conversation_id}`. When a conversation is resumed and re-finalized, the upsert replaces the old vector (idempotent). Moment IDs are `moment:{conversation_id}:{i}`, also deterministic so re-finalization upserts all siblings.

## Alternative Considered

**Per-turn embedding** (N embeddings per conversation): richer recall, but cost scales O(turns). For a 50-turn conversation, that's 50 embeddings + searches that return a specific turn. Trade VectorStore size for query precision. Deferred as a future optimization if turn-level precision proves essential.

## Status

✅ Implemented in Batch M6. Flagged moments ✅ in later M6 work (optional, default false).
