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

### 🟡 M6 — Memory write paths
- ✅ Read paths (episodic retrieve, factual get) on the hot path.
- ✅ Write *methods* exist: `EpisodicMemory.write`, `FactualMemory.extract` /
  `remember`; the loop **enqueues** them fire-and-forget after `TurnComplete`.
- ⬜ Rolling-summary rollover: `WorkingMemory.needs_rollover` exists but the
  summarizer (oldest turns → `invoke`+`response_model` → fold into summary → drop
  from buffer) is not wired to run.
- ⬜ Robust background-write infrastructure (currently `asyncio.create_task` with
  error suppression; no durability/retry if a write fails).

---

## Not started

### ⬜ Real store backends (behind existing Protocols)
- Redis `SessionStore` (SPEC §9.1: hash per `session:{conversation_id}`, idle TTL).
- SQLite `ProfileStore` + `PermissionStore` via SQLAlchemy + aiosqlite (§9.2;
  Postgres = connection-string change).
- Qdrant `VectorStore` (§9.3: collection, always `user_id`-filtered).
- Stubs exist in `stores/stubs.py`; the same contract tests should run against them.

### ⬜ M8 — Offline jobs (`llm_kit` batch engine)
- Nightly memory consolidation (summarize/dedupe/decay episodic points), bulk
  re-embedding of a knowledge base, eval runs over conversation logs.

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

## Open questions (SPEC §16) — current stance
- **Transcript durability**: deferred. Redis working buffer + episodic are the only
  retention for now; no `messages` table yet.
- **Multi-tenant isolation** beyond `user_id`: deferred (per-tenant Qdrant
  collections if/when needed).
- **Query rewrite default**: off by default; per-deployment toggle exists.
- **Episodic granularity**: one point per turn currently; per-session-summary is a
  future option affecting precision and Qdrant growth.
