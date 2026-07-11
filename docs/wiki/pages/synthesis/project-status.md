---
title: Project Status & Milestones
category: synthesis
tags: [roadmap, milestones, status, deliverables, scope]
created: 2026-07-11
updated: 2026-07-11
sources: [ROADMAP.md, CLAUDE.md]
status: current
---

# Project Status & Milestones

## Completed (✅)

**Core foundations (M1–M7, M9–M11):** Runnable vertical slice with multi-user enforcement built-in from day one.

- **M1–M4:** Skeleton, config, stores (in-memory + real adapters), agent loop, context assembly & budgeting
- **M5:** Tools (native + MCP), permissions, per-tool policies
- **M6:** Memory write paths (factual extraction, working-memory rollover, episodic finalization at conversation-end)
- **M7:** Serving (WebSocket + SSE), composition root
- **M9:** Observability (telemetry/Langfuse via seam, metrics/Prometheus via seam, span tree)
- **M10:** Per-tool configuration (timeout override, rate-limiting, HITL approval gates)
- **M11:** Conversation listing API with metadata

**Real store backends:** Redis SessionStore (with indexes), SQLite Profile/Permission (Postgres-compatible), Qdrant VectorStore (in-memory/file/remote modes)

**Extended features:**
- **M8:** Offline jobs (dedup via cosine+Union-Find, resummarize via batch API, `forget_memory` tool)
- **Live integration testing:** Seven coverage areas verified with real LLMs
- **Skills (M6 extended, v1 complete):** File-based agentskills.io format, progressive disclosure, tier-0 context assembly
- **Flagged moments:** Optional per-conversation discussion-thread embeddings (off by default)
- **Per-conversation model switching:** Caller-driven LLM override per session

## Deferred (nice-to-haves)

**Per-turn tool-subset selection (§6.3):** Scaling guard, inert until tool counts are large. Recommended three-phase design:

1. **Threshold gate (build first):** if `len(allowed_tools) <= 25`, send all tools (today's reality).
2. **Embedding-based retrieval:** rank tools by cosine similarity at turn time, piggyback on episodic query embedding.
3. **Progressive disclosure meta-tool:** expose core tools + `search_tools(query)` for on-demand MCP tool discovery (highest behavioral change, hardest to keep deterministic).

Constraint: no synchronous round-trip in front of first LLM call (TTFT is harness_kit's identity).

**Horizontal scaling:** Already near-stateless behind Protocols. Add workers + swap SQLite→Postgres. Maintain invariant: no module above `stores/` caches mutable per-user state in-process.

**Skills v2:** Per-user skill grants via `SqliteSkillStore` adapter (scaffolding complete). Script execution deferred (security decision: sandboxing + approval gates needed first).

**Transcript durability:** No `messages` table yet. Redis working buffer + episodic embeddings are current retention.

**Multi-tenant isolation:** Per-tenant Qdrant collections if/when needed (beyond `user_id` scoping).

## Settled Design Decisions

- **Rolling-summary trigger:** Token-budget-driven, not fixed turn count
- **Episodic embedding:** Conversation-end (not per-turn), per-conversation (not per-turn)
- **Two-stage idle lifecycle:** `idle_finalize_s` (embed, resumable) → `ttl_s` (evict session)
- **Age decay:** Runtime (retrieve-time), not batch rewrite; always fresh
- **Telemetry & metrics:** Vendor-neutral seams (Langfuse/Prometheus swappable)
- **Dedup:** Cosine similarity + Union-Find, handles transitivity, no heavy dependencies

## Open Questions (SPEC §16)

- Transcript durability — deferred
- Multi-tenant isolation — deferred
- Query rewrite default — off by default, per-deployment toggle exists

## Summary

**Shipped:** Stateful, multi-turn agentic chatbot with conversation state, three-part memory (working/episodic/factual), tool-calling agent loop with safety rails, real backends (Redis/SQLite/Qdrant), offline jobs, observability, skills system, and per-conversation model switching. Multi-user enforcement threaded through every layer.

**Not shipped:** Per-turn tool ranking, transcript durability, horizontal scaling automation, skills v2, shell execution. These are scaled features or security hardening, not blocking the reference implementation.
