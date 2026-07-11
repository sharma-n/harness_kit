# Wiki Log

Append-only chronological record of ingest, query, and lint operations on the wiki.

Entry format: `## [YYYY-MM-DD] <op> | <title>`, where `op ∈ {ingest, query, lint}`.
Use `grep "^## \[" docs/wiki/log.md | tail -N` for recent activity.

## [2026-07-11] ingest | Batch 1 — Foundations

**Source:** CLAUDE.md (§What this is, Layering, Multi-user, Async, Key abstractions)

**Created pages:**
- `entities/harness-kit-overview.md` — system identity and integration points
- `entities/llm-protocols.md` — abstraction layer over llm_kit
- `concepts/bottom-up-layering.md` — strict import discipline
- `concepts/multi-user-scoping.md` — foundational user isolation
- `concepts/async-end-to-end.md` — async throughout
- `concepts/agent-event-stream.md` — streaming abstraction
- `concepts/tool-errors-as-observations.md` — error handling model

**Cross-links:** Intra-batch links complete; forward refs to agent-loop, memory, tools (batches 2-3, 5 following).

**Note:** Code ingest mode. No `docs/raw/` source pages created.

## [2026-07-11] ingest | Batch 2 — Agent Loop & Context Assembly

**Source:** src/harness_kit/agent/{loop,context,budgeter}.py

**Created pages:**
- `entities/agent-loop.md` — SPEC §5: context → invoke → tool exec → repeat; streaming, safety rails
- `entities/context-builder.md` — Assembles five sources in deliberate order; user-scoped
- `entities/context-budgeter.md` — Tiered eviction (tier-0 never drops, then factual, buffer, episodic)
- `concepts/context-assembly-order.md` — Why order matters; factual before episodic before buffer

**Cross-links:** Linked backward to batch 1 concepts/entities; forward to batch 3 (memory) and batch 5 (tools).

**Note:** Code ingest mode.

## [2026-07-11] ingest | Batch 3 — Memory Subsystem

**Source:** src/harness_kit/memory/{working,episodic,factual}.py + CLAUDE.md§Memory design decisions

**Created pages (8):**
- `entities/working-memory.md` — Conversation buffer; token-driven rollover
- `entities/episodic-memory.md` — Per-conversation embeddings; flagged moments; age-decay
- `entities/factual-memory.md` — User profile; extracted facts; tier-0 system message
- `decisions/rolling-summary-rollover.md` — Token-budget trigger vs fixed turn count
- `decisions/episodic-embedding-granularity.md` — Per-conversation (not per-turn) trade-off
- `decisions/two-stage-idle-lifecycle.md` — Finalize then evict; embeddings persistent
- `concepts/background-writes-fire-and-forget.md` — Off-hot-path memory ops; retry; idempotent
- `synthesis/memory-system-overview.md` — Three-part model integrated in context assembly

**Cross-links:** Memory entities reference context assembly; context-budgeter back-linked; forward refs to stores, tools, serving.

**Note:** Code ingest mode. Synthesis page ties working/episodic/factual together conceptually.

## [2026-07-11] ingest | Batch 4 — Stores & Real Backends

**Source:** src/harness_kit/stores/ + ROADMAP.md#Real store backends

**Created pages (2):**
- `entities/stores-overview.md` — Five store Protocols (Session, Profile, Permission, Vector, Skill); async throughout
- `decisions/real-store-backends.md` — Redis (SessionStore), SQLite (Profile/Permission), Qdrant (VectorStore) with user-scoping verification

**Cross-links:** Stores entities reference memory (working/episodic/factual); forward links to jobs (delete/list_points for M8).

**Note:** Code ingest mode.

## [2026-07-11] ingest | Batch 5 — Tools, Permissions, MCP

**Source:** src/harness_kit/tools/{registry,native,mcp,ratelimit}.py + CLAUDE.md (MCP gotchas, HITL approval)

**Created pages (6):**
- `entities/tool-registry.md` — Central registry; two responsibilities (definitions + execution); per-tool policies
- `entities/native-tools.md` — Five hardcoded tools (remember_fact, forget_fact, list_facts, recall, forget_memory) + read_skill; seeded in default allowlist
- `entities/mcp-integration.md` — Bring-your-own servers; discovery; namespacing ({server}__{tool}); auto-allow policies
- `concepts/permission-model.md` — Two-check pattern (definition-time + execute-time); default allowlist + per-user overrides; user-scoped guarantee
- `decisions/rate-limiting-in-process.md` — Token buckets per (user, tool); non-blocking reject; in-process caveat; bounded LRU (v1.7)
- `decisions/hitl-approval-gates.md` — Optional approval before execution; WS (async) vs. SSE (auto-deny) flows; approval futures in-process; timeout behavior

**Cross-links:** Tool registry references agent-loop, context-assembly (tool defs in tier-0), memory (remember_fact/recall), permission-model, rate-limiting, approval-gates, native-tools, mcp-integration. Native tools back-link to memory entities, skills, permission-model. MCP back-links to tool-registry, native-tools, permission-model. All tools pages cross-reference each other and agent-loop.

**Note:** Code ingest mode. Tool namespacing (double-underscore) and MCP lazy-import patterns documented.

## [2026-07-11] ingest | Batch 6 — Skills System

**Source:** src/harness_kit/skills/{loader,manager}.py + CLAUDE.md#Skills design decisions

**Created pages (3):**
- `entities/skills-system.md` — Filesystem-based skills (agentskills.io format); metadata-only startup discovery; live body updates without restart
- `concepts/progressive-disclosure.md` — Three-stage loading (metadata/activation/references); balances startup latency and context cost
- `decisions/skills-v1-v2-scaffolding.md` — SkillStore Protocol future-proof for per-user grants; `allowed-tools` parsed (not auto-granted); layering with permissions

**Cross-links:** Skills entity references context-builder, progressive-disclosure, permission-model, native-tools (read_skill). Progressive-disclosure back-links to context-budgeter (tier-0 and tier-3). V1-V2 scaffolding references PermissionStore and future roadmap.

**Note:** Code ingest mode. Scaffolding for v2 documented without implementation (deferred feature).

## [2026-07-11] ingest | Batch 7 — Serving & Model Switching

**Source:** src/harness_kit/serving/{app,wire}.py + src/harness_kit/service.py + CLAUDE.md#Per-conversation model switching

**Created pages (3):**
- `entities/serving-layer.md` — FastAPI app (WebSocket /ws + SSE /sse); backpressure via awaited sends; idle sweeper; model-switch + approval commands; auth stub
- `entities/service-composition-root.md` — AgentService as composition root; one shared HTTP client; store wiring; LLM factory; lifetimes (build/astart/aclose)
- `decisions/per-conversation-model-switching.md` — Model override in SessionState; two-gate resolution (factory + override); per-model LLM caching; WS/REST endpoints; O(1) lookup

**Cross-links:** Serving layer references agent-loop, agent-events, two-stage-idle-lifecycle, permission-model (model-switch auth), service-composition-root. Service root links to stores-overview, llm-protocols, context-builder, tool-registry, all memory entities, and agent-loop. Model-switching cross-references agent-loop, context-builder, serving-layer, llm-protocols.

**Note:** Code ingest mode. WS/SSE transport differences and approval flow integration documented.

## [2026-07-11] ingest | Batch 8 — Observability (Telemetry & Metrics)

**Source:** src/harness_kit/{telemetry,metrics}.py + CLAUDE.md#Telemetry / tracing (Langfuse)

**Created pages (3):**
- `entities/telemetry.md` — Vendor-neutral seam over Langfuse (v4 built on OTel); off by default; span tree (turn→context→llm→tool); trace identity (conv_id→session, user_id→user); streaming rule (no buffer)
- `entities/metrics.md` — Vendor-neutral seam over Prometheus; off by default; five instruments (ttft, turn_latency, turn_iterations, tool_calls_total, retrieval_hits); /metrics endpoint; process-level registry
- `decisions/vendor-neutral-telemetry-seam.md` — Leaf module pattern (both only files importing vendors); off-by-default no-op; future OTel/StatsD swaps require reimplementing two files; context propagation via OTel

**Cross-links:** Telemetry entity references agent-loop (run_turn spans), context-builder, tool-registry, memory (background writes), serving (TTFT). Metrics references agent-loop, tool-registry, episodic-memory. Both entities reference vendor-neutral-seam decision. Seam decision links to bottom-up-layering and discusses future OTel path.

**Note:** Code ingest mode. No-op disabled default and lazy imports documented.

## [2026-07-11] ingest | Batch 9 — Offline Jobs (M8)

**Source:** src/harness_kit/jobs/{__main__,_base,dedup,resummarize}.py + CLAUDE.md#Offline jobs design decisions

**Created pages (3):**
- `entities/jobs-offline.md` — CLI batch tools (dedup, resummarize); layering (alongside serving, imports stores/config/llm_kit directly); user-scoped operations; future automation possibilities
- `decisions/dedup-clustering-approach.md` — Cosine similarity + Union-Find (not HDBSCAN); handles transitivity; pairwise matrix; LLM merge per cluster; idempotent; moment sibling deletion
- `decisions/age-decay-runtime-not-batch.md` — Runtime decay (not batch rewrite); freshness without writes; tunable decay_rate; prevents stale context dominance; zero maintenance

**Cross-links:** Jobs offline references stores-overview (delete/list_points), episodic-memory, service-composition-root. Dedup-clustering links to jobs-offline and discusses similarity_threshold config. Age-decay references episodic-memory (retrieve), context-builder, and contrasts with dedup-clustering. All three reference memory-system-overview.

**Note:** Code ingest mode. User-scoping via store calls and idempotency patterns documented.

## [2026-07-11] ingest | Batch 10 — Project Status & Deferred Work

**Source:** ROADMAP.md (done/deferred/nice-to-haves sections, settled decisions, open questions) + SPEC.md§6.3, §16

**Created pages (2):**
- `synthesis/project-status.md` — Milestone snapshot (M1-M11 ✅, M8 ✅, integration tests ✅, skills v1 ✅); deferred: per-turn tool selection (adoption-gated), scaling, skills v2, transcript durability, multi-tenant; settled decisions and open questions listed
- `decisions/deferred-tool-subset-selection.md` — Scaling guard (adoption-gated, inert at tool count <25); three phases (threshold gate, embedding-based retrieval, meta-tool escalation); latency-constrained (zero hot-path delay); layering (selector in context-assembly, not registry)

**Cross-links:** Project-status references all major subsystems (agent-loop, memory, tools, skills, serving, observability, jobs) and deferred features. Deferred-tool-subset cross-references tool-registry, context-builder, context-budgeter, episodic-memory, serving-layer. Both link to relevant decision/entity pages for drill-down.

**Note:** Code ingest mode. Status page is a milestone snapshot (dated 2026-07-11); deferred page is design guidance for future work, not implemented yet.

## [2026-07-11] ingest | Batch 11 — Testing & Operations

**Source:** CLAUDE.md (Testing posture, Running things, Live integration tests)

**Created pages (3):**
- `concepts/fake-driven-testing.md` — FakeLLM approach mirroring llm_kit; scripted turns; golden test discipline; offline unit tests + opt-in live tests
- `entities/running-harness-kit.md` — Setup (uv sync), unit tests (pytest), single-turn example, FastAPI server, environment variables, offline jobs CLI
- `entities/integration-testing.md` — Opt-in live LLM tests (LIVE_TESTS_ENABLED=1), setup with config_live.yaml, seven coverage areas (streaming, tool roundtrip, native tools, memory, skills, extraction, skills integration)

**Cross-links:** Fake-driven-testing references integration-testing (opt-in suite), context-builder (golden test), and agent-loop. Running-harness-kit references context-builder, service-composition-root, serving-layer, and jobs-offline. Integration-testing links back to agent-loop, context-builder, native-tools, factual-memory, episodic-memory, skills-system, and fake-driven-testing.

**Note:** Code ingest mode. Three pages complete the testing and operations story: philosophy (offline fake-driven), running (setup and examples), and verification (live integration testing).

## [2026-07-11] lint | Broken Link Fixes

**Issues found (3):**
- `entities/serving-layer.md` line 21: `[[pages/agent/events|AgentEvent]]` → fixed to `[[pages/concepts/agent-event-stream|AgentEvent]]` (no separate events page exists; AgentEvent is defined in agent-event-stream concept)
- `decisions/deferred-tool-subset-selection.md` line 19: `[[pages/entities/memory|memory system]]` → fixed to `[[pages/synthesis/memory-system-overview|memory system]]` (no standalone memory entity; use synthesis page)
- `entities/integration-testing.md` line 62: `[[pages/entities/memory|memory]]` → fixed to `[[pages/synthesis/memory-system-overview|memory]]` (same issue)

**Status:** All broken links repaired. No orphan pages, no missing index entries. Full cross-link verification passed. Wiki is consistent and navigable.
