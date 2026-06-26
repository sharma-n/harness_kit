# ROADMAP — `agent_kit`

Status of the build against [SPEC.md](SPEC.md)'s milestones (§14). The first pass
delivered a **runnable vertical slice** (in-memory stores, streaming loop, context
builder, serving) with multi-user enforcement built in from the start.

Legend: ✅ done · 🟡 partial / scaffolded · ⬜ not started

---

## Done

### ✅ M1 — Skeleton + config + store Protocols
- `src/` layout, `uv` project, deps + optional extras (redis/sqlite/qdrant/mcp/dev).
- `config/`: `AgentKitConfig` dataclass tree + YAML loader with `${VAR}`
  interpolation; nested `llm_kit` block delegated to `AppConfig.from_dict`.
- `stores/base.py`: `SessionStore`, `ProfileStore`, `VectorStore`, `PermissionStore`
  Protocols. `errors.py` hierarchy.

### ✅ M2 — Stores (in-memory reference adapters)
- In-memory `SessionStore` (user-owned + idle TTL), `ProfileStore`, `VectorStore`
  (numpy cosine, always user-filtered), `PermissionStore` (default-allowlist
  fallback). `factory.build_stores` selects backend from config.
- Contract + **multi-user isolation** tests (cross-user load blocked, search
  isolation, permission scoping).

### ✅ M3 — Agent loop, streaming, working memory
- `agent/events.py` (`AgentEvent`), `agent/loop.py` (`run_turn`): `invoke_stream` →
  event stream, tool loop driven off `StreamEnd.response.tool_calls`.
- Safety rails: `max_iterations` cap, tool-error-as-observation, per-tool timeout,
  optional per-turn wall-clock budget.
- Working memory read + synchronous append; streaming verified end-to-end via fakes.

### ✅ M4 — Context construction + budgeter
- `agent/context.py` assembles the five sources in SPEC §6.2 order; `agent/budgeter.py`
  does tiered eviction (§6.5). Episodic query augmentation (§6.4) implemented;
  optional query-rewrite gated by config.
- Golden test of the §6.6 worked example + budgeter tier-eviction tests.

### ✅ M5 — Tools / MCP
- `ToolRegistry` (user-scoped definitions + execute, per-tool timeout, truncation),
  native factual tools `remember_fact` / `forget_fact` / `list_facts` and episodic
  `recall`, loop integration, safety rails.
- Real MCP client (`tools/mcp.py`): operators bring their own MCP servers
  (stdio / streamable-HTTP / SSE). `MCPServerClient` connects + discovers; `MCPManager`
  aggregates across servers, **best-effort** (a server that fails to connect within
  `mcp.startup_timeout_s` is logged and skipped). Discovered tools are wrapped as plain
  `Tool`s and registered into the existing registry, namespaced `{server}__{tool}`
  (double underscore — provider-safe + collision-safe vs single `_`). Connection
  lifecycle runs in `AgentService.astart()` / `aclose()`, driven from the serving
  lifespan. Permissions stay per-user: discovered tools are unreachable until
  allowlisted, with an opt-in per-server `auto_allow` that folds a trusted server's
  tools into the default allowlist (`PermissionStore.extend_default_allowed`).
- Per-turn tool-subset selection (§6.3) is intentionally **not** built yet — it is a
  scaling guard, not a missing feature (see Nice to haves).

### ✅ M6 — Memory write paths
- Read paths (episodic retrieve, factual get) on the hot path.
- Factual write paths: `FactualMemory.extract` / `remember`; the loop **enqueues**
  extraction fire-and-forget after `TurnComplete`.
- Rolling-summary rollover (token-budget-driven): `WorkingMemory.maybe_rollover`
  summarizes the oldest turns (`invoke`+`response_model` → fold into summary → drop
  from buffer) when the buffer exceeds `buffer_token_budget`; enqueued after each turn.
- Episodic embedding deferred to conversation end: `EpisodicMemory.write_conversation`
  embeds the whole conversation as one point (deterministic per-conversation id, so a
  later re-finalize upserts rather than duplicates). No per-turn embedding.
- Transport-agnostic conversation-end trigger: a **two-stage idle lifecycle** on the
  session — `idle_finalize_s` (embed the conversation, keep it resumable) then `ttl_s`
  (evict). `Agent.end_conversation` fires on WS disconnect (fast path) **and** from a
  background idle sweeper (`Agent.sweep_idle`, started in the serving lifespan) that
  covers SSE (no disconnect signal) and abrupt WS drops. `finalized_at` makes finalize
  idempotent and is cleared on new activity so resumed conversations re-finalize.
- Robust background-write infrastructure: logging (choke point + sweep_idle + WS
  disconnect) + store-write retry (exp backoff + jitter, wraps only store calls so
  LLM/embedder never re-run). Tunable via `MemoryConfig.store_retry`; all background
  store ops verified idempotent (except append-only `append_turn`).

### ✅ M7 — Serving
- `serving/app.py`: FastAPI websocket + SSE; `serving/wire.py` event encoder;
  `service.py` composition root sharing one httpx session across LLM + embedder.
- ws/SSE end-to-end tests incl. cross-user `Unauthorized` frame.

### ✅ Cross-cutting (brought forward) — Multi-user
- User-owned sessions, user-scoped memory, per-user tool permissions enforced at
  both tool selection and execution. (SPEC treated this as §user_id filtering; we
  made it a first-class, tested invariant from day one.)

### ✅ M10 — Per-tool configuration
- Per-tool execution policy (`ToolPolicy`, keyed by tool name) under
  `tools.definitions` in config: **per-tool timeout override** (falls back to
  `agent.per_tool_timeout_s`) + **per-user rate limit** (`rate_limit_per_minute`).
  `ToolRegistry.execute()` applies both; a timed-out or rate-limited call becomes a
  `ToolResult(ok=False)` observation (tool errors are observations, SPEC §5) — no
  loop change, no new exceptions. Config parses with no loader change (`_coerce`
  already handles `dict[str, ToolPolicy]`).
- Rate limiting is a **simple in-process** token bucket (`tools/ratelimit.py`),
  per-`(user_id, tool)`, **reject-on-exceed** (non-blocking, so it never stalls
  time-to-first-token). Mirrors `llm_kit`'s own in-process `TokenBucket` posture;
  documented multi-worker caveat (effective ceiling ≈ workers × the configured rate).
  A shared-store (Redis) backing is a later scaling step, not needed now.
- **Human-in-the-loop (HITL) approval gates** (`requires_approval: bool`,
  `approval_timeout_s: float` on `ToolPolicy`). When set, the agent loop pauses
  before executing the tool and emits `ToolApprovalRequired(call_id, name, arguments,
  timeout_s)`. Over **WebSocket**: the WS handler (now two concurrent coroutines via
  `asyncio.gather` — `_receive` + `_run_turns`) routes an incoming
  `{"type":"approval","call_id":…,"approved":bool}` message to
  `Agent.resolve_approval()`, which resolves an `asyncio.Future` the loop is
  awaiting. On approval → `ToolCallStarted` + normal execute. On denial or timeout →
  `ToolResult(ok=False)` with a distinct reason fed back to the model as an
  observation ("user denied approval" vs "approval request timed out"). Over **SSE**:
  automatically denied (SSE is one-way); the loop's future is resolved to `False`
  immediately. Futures are in-process (same documented-caveat class as the rate
  limiter; WS connections are typically sticky so this is safe for single-worker and
  sticky-LB multi-worker deploys).

### ✅ M11 — Conversation listing & metadata API
- `GET /conversations?user_id=...` → `{conversations: [...]}` of transcript-free
  `ConversationMeta` (`id, user_id, created_at, updated_at, finalized_at, turn_count,
  summary_preview`), newest-first, user-scoped. Optional `status` (active/finalized)
  and `limit` filters. Backed by a new `SessionStore.list()` Protocol method
  (in-memory impl + Redis stub signature); added `created_at` to `SessionState`.
- **Forward-compatible for durable transcripts:** `SessionStore` is hot, TTL'd state,
  so it is not the home for full transcripts. The metadata contract is stable and
  transcript-free, so a future `TranscriptStore` Protocol + a `GET /conversations/{id}`
  detail route returning a `ConversationDetail` (= `ConversationMeta` + `turns`) drops
  in without reworking the listing path. Summary preview is sufficient for now.

### ✅ Real store backends (behind existing Protocols)
- **Redis `SessionStore`** (SPEC §9.1): JSON blob at `session:{conv_id}` with idle-reset
  `EXPIRE`; two side-index ZSETs (`user:{uid}:convs` for listing, `sessions:pending_finalize`
  for idle sweeper) avoid full-key SCAN. `redis.asyncio` client.
- **SQLite `ProfileStore` + `PermissionStore`** (§9.2): SQLAlchemy Core + aiosqlite;
  Postgres = connection-string swap, no code change. Default permission fallback stored as
  `user_id='__default__'` sentinel row.
- **Qdrant `VectorStore`** (§9.3): `AsyncQdrantClient` with three modes: `memory` (in-process,
  no Docker), `file` (local persistence), `host` (remote). Always `user_id`-filtered via
  `FieldCondition`. String point IDs mapped to deterministic UUID5 (stored as `_ak_id` in
  payload, recovered on retrieval). `QdrantConfig` extended with `mode`, `path`, `vector_size`.
- `stores/stubs.py` replaced by a thin re-export shim; real adapters live in
  `redis_session.py`, `sqlite_profile.py`, `sqlite_permissions.py`, `qdrant_vectors.py`.
- `tests/test_stores_real.py`: 17 new contract tests. SQLite + Qdrant always run (no Docker);
  Redis tests skip if port 6379 is unreachable.

### ✅ M9 — Observability + cost accounting
- **Distributed tracing (Langfuse, OpenTelemetry-based).** Vendor-neutral leaf seam
  `telemetry.py` (the only `langfuse` import); off by default → no-op, so the default
  suite stays offline. Full span tree per turn: `turn` (root, `conversation_id`→session
  / `user_id`→user) → `context.build` (+ working/factual/episodic/tools reads) →
  per-iteration `llm.invoke_stream` *generation* → `tool.execute:{name}` (outcome tag);
  background extract/rollover spanned in `_guard` (same trace via the OTel context that
  `asyncio.create_task` copies); `conversation_end` subtree on finalize. `TracingLLM`/
  `TracingEmbedder` capture every generation as the single chokepoint (also the memory
  layer's direct calls). New `telemetry` extra; `tests/test_telemetry.py` drives a
  recording double offline (no-op + stream pass-through + span-tree assertions).
- **Cost accounting** rides on the generations: model + `TokenUsage` (input/output/
  total) are stamped on each, so Langfuse prices per trace/user/conversation from its
  model tables — no separate `UsageLedger` wiring needed for the common path.
- **Metrics pillar.** Prometheus `/metrics` via `prometheus_client` (optional `metrics`
  extra). Five instruments: `agent_kit_ttft_seconds` (Histogram), `agent_kit_turn_latency_seconds`
  (Histogram), `agent_kit_turn_iterations` (Histogram), `agent_kit_tool_calls_total`
  (Counter, labels `tool`+`outcome`), `agent_kit_retrieval_hits` (Histogram). Same seam
  pattern as `telemetry.py`: single `metrics.py` leaf, no-op by default
  (`MetricsConfig.enabled=false`), `_set_instruments_for_test` for offline tests.
  `/metrics` returns 501 JSON when disabled, Prometheus text format when enabled.

---

## Done (continued)

### ✅ Skills — File-based capability extensions (agentskills.io format)

**V1 — implemented:**
- `SKILL.md` parser (`skills/loader.py`): YAML frontmatter (`name`, `description`,
  optional `allowed-tools`) + Markdown body. Discovery is best-effort — malformed or
  missing files are logged and skipped (consistent with MCP's startup posture).
- `SkillManager` (`skills/manager.py`): in-memory index of discovered skills.
  `metadata_block(allowed, header)` emits a ~50-token "Available skills:" list per
  visible skill; `read_body(name, allowed)` reads the full body from disk on demand
  (no body cache — operators can update files without restart).
- `SkillStore` Protocol (`stores/base.py`) + `InMemorySkillStore`: per-user skill
  visibility grants. `allowed_skills()` returns `None` → all skills globally visible
  (v1 default). Built-out now so v2 per-user grants require only a new adapter.
- `SkillsConfig.paths` in `AgentKitConfig`: directories to scan at startup (sync
  filesystem I/O in `AgentService.build()`, safe without async).
- `read_skill(name)` native tool (`tools/skill_tools.py`): agent-driven progressive
  disclosure. Permission is re-checked at execution time (defense-in-depth).
  Pre-seeded into `PermissionStore` default allowlist when skills are active.
- Context assembly: skills block injected into system message as tier-0 (never
  evicted): `base_prompt → dynamic → skills_block → factual → episodic → summary`.
- `AgentConfig.skills_block_header` for customization.

**Design decisions:**
- Skills are files on disk, never in any database. `SkillStore` stores only grant state.
- `allowed-tools` is parsed but not auto-granted — tool permissions remain in
  `PermissionStore` (the authorization boundary). Operator must grant explicitly.
- Script execution (`scripts/` directories) is not supported in v1 — no vetted shell
  tool exists today. Adding one is a deliberate security decision (sandboxing, approval
  gates) deferred to a dedicated milestone.

**V2 — deferred (scaffolding built):**
- `SqliteSkillStore`: per-user skill grants persisted to SQLite. `SkillManager` API is
  identical in v1 and v2 — only the store adapter changes.
- Per-user skill grants via `SkillStore.grant(user_id, skills)` / `revoke()` operator API.
- `SkillPolicy` config block: `auto_grant_tools: bool` — when true, a skill's
  `allowed-tools` are added to the default `PermissionStore` allowlist at startup
  (opt-in, mirrors MCP `auto_allow`).
- Script execution: when a vetted shell tool is added (sandboxing + approval gates),
  skills' `scripts/` directories become executable by the agent.

---

## Not started

### ⬜ M8 — Offline jobs (`llm_kit` batch engine)
- Nightly memory consolidation:
  - **Episodic decay**: age-weight older conversation points lower so fresh context
    ranks higher in retrieval without deleting older knowledge entirely.
  - **Episodic deduplication**: semantic clustering — if two conversations are
    sufficiently similar (cosine distance below threshold), merge them into a single
    composite summary to keep the vector store lean.
  - **Episodic re-summarization**: periodically re-embed and re-summarize long
    conversation groups to reduce noise and keep embeddings current as user profile
    evolves (e.g., quarterly pass over user's episodic points).
  - Trade-off: keep recall broad (old context still findable) while prioritizing
    relevance (new context ranks first) and keeping vector store scalable.
- Bulk re-embedding of a knowledge base (when embedder changes or new docs added).
- Eval runs over conversation logs (accuracy metrics, tool invocation patterns).

### ⬜ Live / integration testing (NEW — explicitly planned)
> SPEC §15 says "no live-key integration tests in-repo." That holds **for now**,
> but real-world testing against a live provider **will** be needed.

Plan when we get there:
- A separate, **opt-in** test suite (e.g. `tests/integration/`, marked
  `@pytest.mark.live`, skipped unless a key env var is present) so the default
  `uv run pytest` stays network-free and deterministic.
- Smoke-level coverage: a real single turn streams tokens; a real tool round-trip;
  episodic write→retrieve against a real embedder; provider parity across
  openai/anthropic/gemini `message_format`.
- Keep secrets out of the repo (env/CI secrets only); never commit keys or
  recorded responses containing PII.
- **Blocked locally** until the httpx/OpenSSL `OPENSSL_Applink` issue on this
  Windows box is resolved (see CLAUDE.md) — any `httpx.AsyncClient` currently
  crashes, so the live path can't be exercised here yet.

### ⬜ Horizontal scaling (later, per SPEC §12)
- Already near-stateless behind the store Protocols. Scaling out = add workers +
  swap SQLite→Postgres. Maintain the invariant: no module above `stores/` caches
  mutable per-user state in process without a shared-store backing.

---

## Nice to haves

### ⬜ Per-turn tool-subset selection (§6.3) — scaling guard, adoption-gated
Today the model is offered **every** allowed tool, every iteration. SPEC §6.3 warns
this degrades at scale — 100 MCP tools per turn burns tokens and hurts selection. But
it's **latent, not a missing feature**: agent_kit ships 4 native tools and zero
bundled MCP tools, so the count is whatever an operator wires up. It only bites a
deployment that connects several fat servers. Hence: build it as an opt-in guard that
is **inert until tool counts are large**, never an always-on pipeline stage.

The governing constraint is agent_kit's identity — **TTFT/latency**. Selection runs
*before* the first token, so nothing here may add a synchronous round-trip in front of
the first LLM call. That rules out an LLM router (two-pass) and reshapes the rest.

Recommended design, in build order:
- **Threshold gate (floor, build first):** if `len(allowed_tools) <= N` (e.g. 25),
  send all tools exactly as today — the current 4-tool reality and the golden context
  test stay untouched. Selection only engages above the threshold.
- **Embedding-based retrieval (when a many-tool deployment appears):** embed each
  tool's `name + description` once at `astart()` into a small in-process index; at turn
  time rank by cosine against the query and take top-N (mirrors episodic retrieval,
  reuses the `Embedder` Protocol, same `min_score` idea). Keep marginal latency ~zero
  by **piggybacking on the episodic query embedding** (§6.4 already embeds the
  augmented user message). Select once per turn and hold the subset for the whole loop.
- **Progressive disclosure via a meta-tool (escalation if quality still suffers):**
  expose core/native tools plus a `search_tools(query)` tool; the model pulls in MCP
  tools on demand. Zero hot-path latency (cost is paid lazily as an extra iteration
  only when needed) but the biggest behavioral change and hardest to keep deterministic.

Layering: keep `tools/registry.py` answering only "what is this user *allowed*";
relevance ranking is a context-assembly concern (`agent/context.py` or a small
`agent/tool_selector.py`), sitting upstream of the budgeter's tier-0.

### ✅ Episodic refinement: flagged moments within conversations
**Rationale:** Per-conversation embedding (M6) trades per-turn recall for efficiency.
But a 50-turn conversation embedded as one blob is searchable but noisy — a broad
averaged vector may not surface well when a user asks about a specific topic within it.

**Implemented approach:**
- At conversation-end, the LLM identifies 1–`max_flagged_moments` notable **discussion
  threads** (what the user was working through, problems explored, situations they were in)
  — distinct from facts, which remain in factual memory.
- The whole-conversation point is written as before (`kind="conversation"`). Each flagged
  moment is embedded as a sibling point (`kind="moment"`, `parent_id=conv:{id}`,
  deterministic ID `moment:{id}:{i}` → upserts on re-finalize).
- Both kinds compete naturally in the same `top_k` search — no Protocol change.
  The budgeter handles density via score-based eviction (moment texts are short, so
  they cost fewer tokens and leave room for more results).
- Off by default (`flagged_moments_enabled: false`); opt-in via config. Safe no-op
  when `llm` is None. All moment writes use the existing `store_write` retry path.
- **Guidance clarity also improved** (same PR): `remember_fact` / `recall` tool
  descriptions now explicitly distinguish user attributes (factual) from discussion
  topics (episodic); `extraction_system_prompt` tells the extractor not to emit
  discussion context.

**Trade-off:** 1–N extra embedding calls per conversation finalization (LLM invoke +
embed per moment), all off the hot path. Qdrant/in-memory point count grows by at most
`max_flagged_moments` per conversation. Tunable; 1–3 moments recommended.

---

## Settled design decisions

- **Rolling-summary trigger**: Token-budget-driven. Rollover fires when working buffer
  exceeds budget, not at fixed turn counts, to respect context limits. Respects the
  same tier constraints as context assembly.
- **Episodic embedding strategy**: Conversation-end only (not per-turn). One embedding
  per conversation; cheaper and more scalable. Trades per-turn specificity for
  compactness and cost. Can revisit to per-N-turns or per-turn if finer granularity
  is needed.
- **Two-stage idle lifecycle**: `idle_finalize_s` < `ttl_s`, by construction (config
  validates it). The shorter timer finalizes (embeds) the conversation but keeps the
  session loadable so a returning user resumes seamlessly; the longer timer evicts.
  Finalize is driven by a periodic sweeper (not per-request) so it is transport-agnostic
  — the only way SSE, which has no disconnect signal, gets a conversation-end event.

## Open questions (SPEC §16) — current stance
- **Transcript durability**: deferred. Redis working buffer + episodic are the only
  retention for now; no `messages` table yet.
- **Multi-tenant isolation** beyond `user_id`: deferred (per-tenant Qdrant
  collections if/when needed).
- **Query rewrite default**: off by default; per-deployment toggle exists.
