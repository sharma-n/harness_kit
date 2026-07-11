---
title: Offline Jobs (M8)
category: entity
tags: [jobs, batch, offline, cli, dedup, memory-maintenance]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/jobs/{__main__,_base,dedup,resummarize}.py, CLAUDE.md#Offline jobs design decisions]
status: current
---

# Offline Jobs (M8)

Batch CLI tools for episodic memory maintenance (deduplication and re-summarization). Separate from the serving layer; jobs are CLI scripts, not embedded in FastAPI.

## Layering

`jobs/` sits alongside `serving/` at the top of the layer stack. It imports from:

- `stores/` (to access [[pages/entities/stores-overview|Stores]])
- `memory/` (to instantiate [[pages/entities/episodic-memory|EpisodicMemory]])
- `config/` (to load configuration)
- `llm_kit` directly (concrete clients with `run_batch_stream` and `embed_batch`)

Jobs do **NOT** import from `agent/` or `serving/`. They operate on raw data without the agent loop.

## Commands

```bash
python -m harness_kit.jobs dedup --config config.yaml --users alice,bob
python -m harness_kit.jobs resummarize --config config.yaml --users alice,bob
```

Target user IDs are supplied explicitly (`--users alice,bob`). There is no `--all-users` in v1 because the [[pages/entities/stores-overview|VectorStore Protocol]] has no `list_users()` method.

## Dedup Job

**`EpisodicDeduplicator`** — Clusters near-identical conversation points using [[pages/decisions/dedup-clustering-approach|cosine similarity + Union-Find]]. Each cluster is merged into one via LLM summarization; originals (including moment siblings) are deleted.

**Flow:**
1. Load all episodic points for the user via `VectorStore.list_points(user_id)`
2. Compute pairwise cosine similarities (numpy)
3. Build a graph: edges where `sim >= similarity_threshold`
4. Find connected components via Union-Find
5. For each cluster with >1 point, invoke LLM to merge summaries
6. Delete original points; upsert merged point

## Resummarize Job

**`EpisodicResummarizer`** — Re-summarizes episodic conversation points (e.g., after a conversation ends late and its initial summary is stale). Uses `llm_kit`'s `embed_batch` to re-embed.

**Flow:**
1. Load all episodic points for the user
2. Re-summarize selected points via LLM batch calls
3. Re-embed the new summaries via `embed_batch`
4. Upsert (update) points in the store

## Forget Tool Integration

The `forget_memory` native tool (see [[pages/entities/native-tools]]) uses `VectorStore.delete()` to erase episodic embeddings. Its integration with jobs is indirect: deletes happen at runtime during turns, not in batch.

## User Scoping

All job operations are user-scoped:

- `list_points(user_id)` returns only points belonging to the user
- `delete(point_ids, user_id)` verifies ownership before deleting (see [[pages/decisions/real-store-backends|real-store-backends]])

A job running for user A cannot see or delete user B's points.

## Retry & Idempotency

Jobs use `store_write()` retry policy (same as background memory writes) to handle transient store faults. All operations are idempotent or append-only so re-running a job is safe.

## Future: Automation

Jobs are currently CLI tools (manual invocation). Future versions might add scheduled automation (e.g., CRON triggers after idle finalization) or a background task in the serving lifespan.
