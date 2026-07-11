# Wiki Index

A curated catalog of concepts, decisions, and design rationale for harness_kit. Navigate by category using the links below.

## Sources

External reference material and documents ingested into the wiki.

(No entries yet — add via `/wiki-ingest`.)

## Concepts

Recurring ideas, patterns, and principles that recur across the codebase.

- [[pages/concepts/bottom-up-layering]] — Strict import discipline: each layer only depends on layers below. 2026-07-11
- [[pages/concepts/multi-user-scoping]] — User-owned sessions, user-scoped memory, per-user permissions threaded through every layer. 2026-07-11
- [[pages/concepts/async-end-to-end]] — Everything is async; blocking calls stall all concurrent conversations. 2026-07-11
- [[pages/concepts/agent-event-stream]] — Load-bearing abstraction: TextDelta, ToolCallStarted, ToolResult, TurnComplete streamed to clients. 2026-07-11
- [[pages/concepts/tool-errors-as-observations]] — Tool failures are not exceptions; they are observations fed back to the model. 2026-07-11
- [[pages/concepts/context-assembly-order]] — Deliberate message ordering: factual before episodic, episodic before buffer, summary before current. 2026-07-11
- [[pages/concepts/background-writes-fire-and-forget]] — Memory writes enqueued off hot path, retried on failure, all idempotent. 2026-07-11
- [[pages/concepts/permission-model]] — Two-layer permission checks: filter at definition-time, re-check at execution. Defense-in-depth user scoping. 2026-07-11
- [[pages/concepts/progressive-disclosure]] — Three-stage skill loading: metadata (startup), activation (read_skill), references (on-demand). Balances context size and availability. 2026-07-11
- [[pages/concepts/fake-driven-testing]] — FakeLLM approach mirroring llm_kit; scripted turns; golden test discipline; offline suite + opt-in live tests. 2026-07-11

## Entities

Components, modules, subsystems, and concrete tools.

- [[pages/entities/harness-kit-overview]] — The system: an agentic chatbot built on llm_kit with conversation state, memory, agent loop, and serving. 2026-07-11
- [[pages/entities/llm-protocols]] — Thin Protocols over llm_kit (LLM, Embedder) enabling testability and per-conversation model switching. 2026-07-11
- [[pages/entities/agent-loop]] — The core loop: context → LLM invoke → tool exec → repeat; streaming and tool-calling with safety rails. 2026-07-11
- [[pages/entities/context-builder]] — Assembles five sources (system, profile, episodic, buffer, current) into the provider's message list. 2026-07-11
- [[pages/entities/context-budgeter]] — Tiered eviction under token ceiling: tier-0 (never drop) down to tier-4 (drop first). 2026-07-11
- [[pages/entities/working-memory]] — Session-scoped buffer of turns; evicts oldest via rollover when it exceeds token budget. 2026-07-11
- [[pages/entities/episodic-memory]] — Per-conversation vector embeddings searchable by semantic similarity; runtime age-decay. 2026-07-11
- [[pages/entities/factual-memory]] — User profile (durable facts) extracted from turns; injected into system message. 2026-07-11
- [[pages/entities/stores-overview]] — Five Protocol abstractions (Session, Profile, Permission, Vector, Skill) for persistent storage, all user-scoped. 2026-07-11
- [[pages/entities/tool-registry]] — Central registry for tool definitions and execution; user-scoped; per-tool policies (timeout, rate-limit, approval). 2026-07-11
- [[pages/entities/native-tools]] — Five hardcoded tools: remember_fact, forget_fact, list_facts, recall, forget_memory; plus read_skill for skills access. 2026-07-11
- [[pages/entities/mcp-integration]] — Bring-your-own MCP servers; discovered at startup; namespaced tools integrated into registry; auto-allow policies. 2026-07-11
- [[pages/entities/skills-system]] — Filesystem-based skills in agentskills.io format; progressive disclosure; metadata + body + references; not auto-granting tools. 2026-07-11
- [[pages/entities/serving-layer]] — FastAPI transport (WebSocket + SSE); bidirectional messaging; approval/model-switch commands; idle sweeper; backpressure via awaited sends. 2026-07-11
- [[pages/entities/service-composition-root]] — Wires config→stores→memory→tools→agent; one shared HTTP client; LLM factory for per-model switching; async startup/shutdown. 2026-07-11
- [[pages/entities/telemetry]] — Vendor-neutral seam over Langfuse (built on OTel); off by default; span tree (turn→context→llm→tool); identity mapping (conv_id→session, user_id→user). 2026-07-11
- [[pages/entities/metrics]] — Vendor-neutral seam over Prometheus; off by default; five instruments (ttft, turn_latency, turn_iterations, tool_calls_total, retrieval_hits). 2026-07-11
- [[pages/entities/jobs-offline]] — Batch CLI tools for episodic memory maintenance (dedup, resummarize); user-scoped; VectorStore.delete/list_points; CLI driven, not embedded in serving. 2026-07-11
- [[pages/entities/running-harness-kit]] — Setup via uv, unit tests, single-turn example, FastAPI server, environment variables, offline jobs. 2026-07-11
- [[pages/entities/integration-testing]] — Opt-in live LLM tests gated by LIVE_TESTS_ENABLED=1; setup with config_live.yaml; seven coverage areas (streaming, tool roundtrip, native tools, memory, skills). 2026-07-11

## Decisions

Architectural decisions, design rationale, and tradeoffs.

- [[pages/decisions/rolling-summary-rollover]] — Token-budget-driven eviction; oldest turns roll into summary when buffer exceeds limit. 2026-07-11
- [[pages/decisions/episodic-embedding-granularity]] — Per-conversation (not per-turn) embedding; cheaper but trades precision. Flagged moments refine balance. 2026-07-11
- [[pages/decisions/two-stage-idle-lifecycle]] — Finalize at idle_finalize_s (embed, stay resumable); evict session at ttl_s (embeddings persist). 2026-07-11
- [[pages/decisions/real-store-backends]] — Redis SessionStore (with indexes + idle-reset), SQLite Profile/Permission (Postgres-compatible), Qdrant VectorStore (in-memory/file/remote). 2026-07-11
- [[pages/decisions/rate-limiting-in-process]] — Token buckets per (user, tool) with non-blocking reject; in-process (multi-worker caveat); bounded LRU for memory. 2026-07-11
- [[pages/decisions/hitl-approval-gates]] — Optional approval before tool execution (config-driven); different flows for WS (async) vs. SSE (auto-deny). 2026-07-11
- [[pages/decisions/skills-v1-v2-scaffolding]] — SkillStore Protocol ready for per-user grants (v2); v1 uses all-or-nothing. `allowed-tools` parsed, not auto-granted. 2026-07-11
- [[pages/decisions/per-conversation-model-switching]] — Caller-driven model override stored in session; two-gate check (factory available + override present); WS and REST endpoints; O(1) lookup. 2026-07-11
- [[pages/decisions/vendor-neutral-telemetry-seam]] — Leaf modules for Langfuse and Prometheus; swappable by reimplementing one file per backend; off-by-default (no-op in tests). 2026-07-11
- [[pages/decisions/dedup-clustering-approach]] — Cosine similarity + Union-Find (not HDBSCAN); handles transitivity; minimal dependencies (numpy); O(n²) similarity, LLM merge per cluster. 2026-07-11
- [[pages/decisions/age-decay-runtime-not-batch]] — Decay computed at retrieval time (not batch rewrite); scores always fresh; zero writes; tunable decay_rate; prevents old context dominance. 2026-07-11
- [[pages/decisions/deferred-tool-subset-selection]] — Per-turn tool ranking deferred as scaling guard (adoption-gated, inert until tool count is large); three-phase design (threshold + embedding-based + meta-tool); TTFT-constrained. 2026-07-11

## Synthesis

High-level analysis and cross-cutting synthesis tying multiple topics together.

- [[pages/synthesis/memory-system-overview]] — Three-part memory (working, episodic, factual) integrated in context assembly and background writes. 2026-07-11
- [[pages/synthesis/project-status]] — Milestones completed (M1-M11, M8, integration tests, skills v1); deferred (per-turn tool selection, scaling, v2 features); open questions. 2026-07-11
