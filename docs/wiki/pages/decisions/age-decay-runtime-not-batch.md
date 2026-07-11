---
title: Age Decay at Runtime, Not Batch
category: decision
tags: [episodic-memory, decay, retrieval, runtime-vs-batch, scoring]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/memory/episodic.py, CLAUDE.md#Offline jobs design decisions]
status: current
---

# Age Decay at Runtime, Not Batch

## Decision

Age decay (exponential decay of episodic point scores over time) is computed at retrieval time, not in a batch rewrite job. Scores are always fresh; no batch maintenance needed.

## Algorithm

`EpisodicMemory.retrieve()` applies decay **after fetching candidates**:

```python
def retrieve(user_id, query, top_k):
    # Fetch top_k * 2 candidates (to account for decay culling)
    candidates = vector_store.retrieve(user_id, query, top_k * 2)
    
    # Apply age decay: score * exp(-rate * age_days)
    now = time.time()
    decayed = []
    for hit in candidates:
        age_days = (now - hit.created_at) / 86400
        decayed_score = hit.score * exp(-decay_rate * age_days)
        decayed.append((hit, decayed_score))
    
    # Re-sort and cap at top_k
    decayed.sort(by_score, reverse=True)
    return decayed[:top_k]
```

## Why Runtime, Not Batch

**Simplicity:** No batch job needed. Scores are always current — no stale data.

**Freshness:** A point from 10 days ago is scored lower than one from 1 day ago, *every time*. No catch-up needed after a batch rewrite.

**Zero writes:** The batch job never touches the vector store, avoiding race conditions and consistency issues during serving.

**Tuning:** `decay_rate` can be changed in config without a batch rewrite — the next retrieval uses the new rate.

## Cost

Decay is a multiplication per hit, negligible compared to vector retrieval latency (milliseconds vs. microseconds).

## Implications

- Older conversations naturally fade from recall unless repeatedly re-visited.
- Users can still `recall` explicitly to search past conversations (decay only applies to implicit retrieval in [[pages/entities/context-builder|context assembly]]).
- Over time, the same query returns different results (older points rank lower). This is intentional — prevents stale context from dominating.

## Configuration

`EpisodicMemoryConfig.decay_rate` (default 0.05, in units of 1/day):

- **0.05:** A 20-day-old point scores 37% of its original value (exp(-0.05 * 20) ≈ 0.37)
- **0.01:** Slower decay; 100-day-old point scores 37%
- **0:** No decay (old points rank equally with new ones)

## Vs. Dedup

Unlike [[pages/decisions/dedup-clustering-approach|dedup]] (which rewrites the store), age decay touches nothing. It's a retrieval-time transformation, isolated to the episodic memory layer.
