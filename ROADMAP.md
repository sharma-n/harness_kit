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
- **Deferred** (need a human-in-the-loop pause / auth subsystem not present yet):
  approval gates ("requires user approval") and auth requirements.

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

---

## Not started

### ⬜ Real store backends (behind existing Protocols)
- Redis `SessionStore` (SPEC §9.1: hash per `session:{conversation_id}`, idle TTL).
- SQLite `ProfileStore` + `PermissionStore` via SQLAlchemy + aiosqlite (§9.2;
  Postgres = connection-string change).
- Qdrant `VectorStore` (§9.3: collection, always `user_id`-filtered).
- Stubs exist in `stores/stubs.py`; the same contract tests should run against them.

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

### 🟡 M9 — Observability + cost accounting
- ✅ **Distributed tracing (Langfuse, OpenTelemetry-based).** Vendor-neutral leaf seam
  `telemetry.py` (the only `langfuse` import); off by default → no-op, so the default
  suite stays offline. Full span tree per turn: `turn` (root, `conversation_id`→session
  / `user_id`→user) → `context.build` (+ working/factual/episodic/tools reads) →
  per-iteration `llm.invoke_stream` *generation* → `tool.execute:{name}` (outcome tag);
  background extract/rollover spanned in `_guard` (same trace via the OTel context that
  `asyncio.create_task` copies); `conversation_end` subtree on finalize. `TracingLLM`/
  `TracingEmbedder` capture every generation as the single chokepoint (also the memory
  layer's direct calls). New `telemetry` extra; `tests/test_telemetry.py` drives a
  recording double offline (no-op + stream pass-through + span-tree assertions).
- ✅ **Cost accounting** rides on the generations: model + `TokenUsage` (input/output/
  total) are stamped on each, so Langfuse prices per trace/user/conversation from its
  model tables — no separate `UsageLedger` wiring needed for the common path.
- ⬜ **Metrics pillar (remaining).** OTel metrics / Prometheus `/metrics` (currently a
  stub): p99 TTFT, turn latency, loop iterations, tool error + rate-limit rates,
  retrieval hit rates. Langfuse covers traces, not histograms/counters — this is the
  operational-monitoring piece still to wire.

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

### ⬜ Episodic refinement: flagged moments within conversations
**Rationale:** Per-conversation embedding (M6) trades per-turn recall for efficiency.
But a 50-turn conversation embedded as one blob is searchable but noisy. When the
agent later retrieves it, it re-reads everything to find one detail.

**Approach (future, when usage data justifies):**
- At conversation-end embedding, let the model **flag 1–2 key moments** within the
  conversation (e.g., "User stated their aisle-seat preference" at turn 5; "Booked
  SFO→JFK flight" at turn 35).
- Embed the whole conversation as the main point (current behavior), *plus* embed
  each flagged moment as a sibling point, all tagged with the same `conversation_id`.
- On retrieval, top-k could surface either the main conversation or a flagged moment,
  improving recall *within* a conversation without per-turn noise.

**Trade-off:** More embedding API calls and Qdrant points per conversation, but
cleaner signal than per-turn and finer recall than per-conversation-only. Revisit
if real usage shows users struggle to find details in older conversations.

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
