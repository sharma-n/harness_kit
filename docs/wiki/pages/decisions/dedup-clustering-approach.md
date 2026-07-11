---
title: Dedup Clustering: Cosine Similarity + Union-Find
category: decision
tags: [jobs, dedup, clustering, algorithms, episodic-memory]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/jobs/dedup.py, CLAUDE.md#Offline jobs design decisions]
status: current
---

# Dedup Clustering: Cosine Similarity + Union-Find

## Decision

Use **cosine similarity** (pairwise distances) + **Union-Find** (connected components) instead of HDBSCAN or other clustering libraries. Handles transitivity, minimal dependencies.

## Algorithm

1. **Pairwise cosine similarities:** Compute cosine distance matrix between all episodic points for the user via numpy. O(n²) but fast for typical conversation counts (thousands).

2. **Graph construction:** For each pair, create an edge if `sim >= similarity_threshold` (configured in `DeduplicationConfig`).

3. **Connected components:** Use Union-Find with path compression to group all transitively similar points. A cluster is a connected component.

4. **LLM merge:** For each cluster with >1 point, pass all summaries to the LLM with `_MERGE_SYSTEM_PROMPT` to produce one merged summary.

5. **Delete & upsert:** Delete original points (including moment siblings, via `list_points` + `delete`); upsert merged point under a deterministic ID.

## Why Not HDBSCAN?

- **Dependencies:** HDBSCAN adds a heavy external dependency; cosine + Union-Find uses only numpy (already present for embedding math).
- **Transitivity:** HDBSCAN is density-based and may split transitively-similar points into different clusters. Union-Find correctly groups all connected components.
- **Simplicity:** Union-Find with path compression is O(n · α(n)) ≈ linear; easy to understand and debug.

## Similarity Threshold

`similarity_threshold` (default 0.85) is configurable in `config.yaml`. Higher values → fewer clusters, coarser merges. Lower values → more clusters, finer distinctions.

## Moment Siblings

When a conversation point is merged, all its `moment:{conv_id}:N` siblings are also deleted (they refer to a deleted conversation). The dedup job calls `list_points()` with a filter on `point_id` pattern to find siblings.

## Idempotency

Dedup is idempotent:

- Merged point IDs are deterministic (based on cluster members' IDs)
- Re-running dedup on the same input produces the same output
- If a merge partially succeeded (some deletes failed), retry only attempts to delete the remaining points

## User Scoping

All operations are user-scoped via `list_points(user_id)` and `delete(point_ids, user_id)`. A dedup job for user A only sees/merges user A's points.

## Performance

For a typical user with 50 conversations (1 point each + maybe 5 moments):
- Similarity matrix: 50×50 = 2500 operations (sub-millisecond)
- Union-Find: O(50) (negligible)
- LLM merge: dominant cost (1-2 seconds per cluster via batch API)

Batching merges across users (future optimization) can amortize LLM calls.
