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

---

## In progress / scaffolded

### 🟡 M5 — Tools / MCP
- ✅ `ToolRegistry` (user-scoped definitions + execute, timeout, truncation),
  native `remember_fact` / `recall`, loop integration, safety rails.
- ⬜ Real MCP client (`tools/mcp.py` is a `NotImplementedError` stub): multi-server
  connect, tool discovery, namespacing by server, invocation, timeout handling.
- ⬜ Curated/relevant tool-subset selection per turn (§6.3) to avoid sending 100
  tools every iteration.

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

### ⬜ M9 — Observability + cost accounting
- Spans/metrics: turn latency, time-to-first-token, loop iterations, tool latency,
  retrieval hit rates, per-source token usage. Wire `/metrics` (currently a stub).
- Cost accounting via `llm_kit`'s `UsageLedger` / `TokenUsage`, aggregated per
  user/conversation and priced from the per-1M-token config fields.

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
