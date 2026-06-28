# CLAUDE.md — orientation for `agent_kit`

Read this first. It explains what this repo is, how it's structured, the rules that
keep it coherent, and how to run things. The full design rationale lives in
[SPEC.md](SPEC.md); current status and what's next lives in [ROADMAP.md](ROADMAP.md).

## What this is

A stateful, multi-turn **agentic chatbot service** built **on top of**
[`llm_kit`](https://github.com/sharma-n/llm_kit) (a git dependency). `llm_kit` owns
provider wire formats, streaming `invoke_stream`, structured `invoke`, the embedder,
rate limiting, retries, and the error hierarchy. `agent_kit` adds the three things
`llm_kit` deliberately omits: **conversation state, memory, and a tool-calling agent
loop**, plus a serving layer.

`agent_kit` optimizes for the *opposite* of `llm_kit`: long-lived sessions, latency
(time-to-first-token), per-user state — not batch throughput.

## The non-negotiable rule: strict bottom-up layering

Each layer imports only from layers below it. Do not violate this.

```
config → stores → (skills || tools) → agent → serving
```

`skills/` and `tools/` sit at the same level — neither imports from the other.
`service.py` is the composition root (top) that wires everything from config.
`agent_kit/llm.py` holds thin `LLM` / `Embedder` Protocols over `llm_kit` so every
layer above depends on the Protocol, not the concrete client — that's what lets the
whole stack run against a `FakeLLM` in tests.

If you find yourself wanting a lower layer to import a higher one (e.g. `tools/`
importing `agent/events.py`), don't — pass primitives up instead. The registry
returns a plain `Execution`; the loop maps it to a `ToolResult` event.

## Multi-user is foundational (not a later feature)

This is a hard requirement, threaded through every layer:

- **Sessions are user-owned.** `SessionStore.load(conversation_id, user_id)` raises
  `UnauthorizedError` if the conversation belongs to a different user.
- **Memory is user-scoped.** Profile is per `user_id`; episodic search is always
  filtered by `user_id` — no cross-user leakage.
- **Tool permissions are per-user.** `PermissionStore` resolves each user's allowed
  tool set. The registry filters tool *definitions* by it AND re-checks on *execute*
  (defense in depth). The single global `config.yaml` only sets the default
  allowlist; per-user grants live in the store.

When adding anything that touches user data, ask: is it scoped to `user_id`? If a
new store/cache holds per-user state in process memory without a shared-store
backing, that breaks horizontal scaling (SPEC §12) — don't. (The one deliberate,
documented exception is the M10 tool rate limiter in `tools/ratelimit.py`: per-user
token buckets are in-process, mirroring `llm_kit`'s own limiter — so a multi-worker
deploy enforces ~`workers × rate_limit_per_minute`. A Redis backing is a later
scaling step, noted in the module docstring.)

## Async end-to-end

Everything is `async`. A synchronous DB/network call on the event loop stalls every
concurrent conversation — treat it as a bug. The in-memory stores are async too so
the real (Redis/SQLite/Qdrant) adapters drop in behind the identical Protocol.

## Map of the code

```
src/agent_kit/
  config/      schema.py (dataclass tree) + loader.py (YAML + ${VAR}; nested llm_kit block)
  stores/      base.py (5 Protocols incl. SkillStore) · types.py (records)
               · memory_*.py (in-memory adapters) · stubs.py (real adapters, NotImplementedError)
               · factory.py (backend select; build_stores returns Stores bundle)
  memory/      working.py (buffer + token-budget rollover) · episodic.py (conversation-end
               embed + runtime age-decay + forget_conversation) · factual.py (cognition over the stores)
  skills/      loader.py (SKILL.md parser + discover) · manager.py (SkillManager index)
               · __init__.py (exports)
  tools/       base.py (Tool) · registry.py (user-scoped exec + per-tool policy) · native.py
               · skill_tools.py (read_skill native tool) · ratelimit.py (in-process per-user
               token bucket) · mcp.py (MCPServerClient connect/discover + MCPManager aggregate)
  agent/       events.py (AgentEvent) · context.py (assembly §6.2) · budgeter.py (tiers §6.5)
               · loop.py (run_turn §5 + end_conversation)
  jobs/        _base.py (load_all_user_points) · dedup.py (EpisodicDeduplicator, cosine+Union-Find
               clustering, llm_kit batch merge) · resummarize.py (EpisodicResummarizer, llm_kit
               batch re-summarize + embed) · __main__.py (CLI: python -m agent_kit.jobs)
  serving/     wire.py (AgentEvent→frame) · app.py (FastAPI ws + sse)
  service.py   composition root: config → stores → skills → tools → agent
  llm.py       LLM / Embedder Protocols over llm_kit
  tokens.py    estimate_tokens — leaf estimator shared by memory/ rollover + agent/ budgeter
  retry.py     retry_async / store_write — exp backoff + jitter for store-write retries
  telemetry.py vendor-neutral tracing seam over Langfuse (the only langfuse import) — leaf
  errors.py    AgentKitError hierarchy (reuse llm_kit.LLMError for provider failures)
examples/      single_turn.py (direct) · ws_client.py (over server)
tests/         conftest.py (FakeLLM/FakeEmbedder + make_service) + per-layer tests
config.yaml    one global config; agent_kit sections + nested llm_kit block
```

## Key abstractions to know

- **`AgentEvent`** (`agent/events.py`): `TextDelta | ToolCallStarted |
  ToolApprovalRequired | ToolResult | TurnComplete`. `run_turn` yields these;
  `serving/wire.py` encodes them to JSON frames. This is the load-bearing abstraction
  — a streaming tool loop can't yield bare tokens.
- **The loop drives tools off `StreamEnd.response.tool_calls`.** `llm_kit`'s
  mid-stream `ToolCallStarted` is *name-only*; the assembled calls *with parsed
  arguments* arrive on `StreamEnd`. agent_kit emits its own `ToolCallStarted` (with
  args) at execution time.
- **Tool errors are observations, not exceptions.** A failed/denied/timed-out tool
  becomes `ToolResult(ok=False)` fed back to the model (SPEC §5). The only things
  that raise are `max_iterations` (graceful stop) and `UnauthorizedError`.
- **Context budgeter** evicts by tier: tier-0 (system/current msg/tool defs) never
  drops (→ `ContextOverflowError`); working buffer evicts oldest; episodic drops
  lowest score.

## Memory design decisions

- **Rolling-summary rollover is token-budget-driven** (`WorkingMemory.maybe_rollover`).
  When the verbatim buffer exceeds `WorkingMemoryConfig.buffer_token_budget`, the
  oldest turns are folded into the rolling summary (LLM `invoke` + `RolledSummary`
  response model) and dropped; the newest turns within budget stay. The trigger is
  token-driven (not a fixed turn count) so it holds regardless of turn size. It runs
  **off the hot path** — the loop enqueues it after `TurnComplete` — and is a safe
  no-op (no turns lost) when there's no LLM, nothing to evict, or the summarizer
  returns nothing usable. Sizing uses the shared `tokens.estimate_tokens`.

- **Episodic embedding is per-conversation, not per-turn** (`EpisodicMemory.write_conversation`,
  triggered by `Agent.end_conversation`). At conversation end the rolling summary +
  remaining buffer are embedded as ONE `kind="conversation"` point — cheaper and more
  compact than per-turn, trading per-turn recall precision for conversation-level memory.
  The point id is deterministic (`conv:{conversation_id}`) so re-finalizing a resumed
  conversation upserts rather than duplicating.

  When `EpisodicMemoryConfig.flagged_moments_enabled` is true, the LLM additionally
  identifies 1–`max_flagged_moments` notable **discussion threads** and embeds each as a
  sibling `kind="moment"` point (`moment:{conversation_id}:{i}`). These compete naturally
  with the conversation point in `top_k` search — no Protocol change, no context-assembly
  change. This is the two-layer balance: the conversation point handles broad "what was
  this conversation about?" recall; moment points handle precision recall for specific
  topics. Off by default; safe no-op when `llm` is None.

- **Conversation end is a two-stage idle lifecycle, not a single TTL** (config
  validates `idle_finalize_s < ttl_s`). `idle_finalize_s` fires first: the conversation
  is embedded but the session stays loadable so a returning user resumes seamlessly;
  `ttl_s` then evicts **the session from SessionStore only** — embeddings remain in
  VectorStore indefinitely (there is no TTL enforcement or deletion in the VectorStore
  Protocol; embeddings are write-only). `end_conversation` is best-effort and
  **idempotent** — missing/expired session or non-owner caller → no-op;
  `SessionState.finalized_at` (cleared on any new activity) stops re-embedding until
  the conversation is resumed. It is driven from two places: **WebSocket disconnect**
  in `serving/app.py` (fast path) and a **background idle sweeper**
  (`Agent.sweep_idle`, started in the serving lifespan, cadence `sweep_interval_s`).
  The sweeper is what gives **SSE** — which has no disconnect signal — a
  conversation-end event, and also catches abrupt WS drops.

- **Background writes are fire-and-forget with logging + retry** — `extract`, `maybe_rollover`,
  `mark_finalized`, and `write_conversation` are enqueued via `Agent._enqueue()` and run
  off the hot path. A failure is no longer silent: `_guard()` logs one ERROR with
  operation + `user_id` + `conversation_id`; `sweep_idle` logs per-conversation WARNING
  and continues (no cascade). Store-write retries (via `retry.store_write()`) wrap **only**
  the store call, not the preceding LLM/embedder step — that's already retried by llm_kit,
  so a transient store fault never re-runs the model. Tunable via `MemoryConfig.store_retry`.
  All background store ops are verified idempotent (except append-only `append_turn`).

## Offline jobs design decisions (M8)

- **`jobs/` sits alongside `serving/`** at the top of the layer stack. It imports from
  `stores/`, `memory/`, `config/`, and directly from `llm_kit` (concrete clients with
  `run_batch_stream` / `embed_batch`). It does NOT import from `agent/` or `serving/`.
  Jobs are CLI scripts, not embedded in the FastAPI lifespan.

- **VectorStore Protocol gained `delete` and `list_points`**. The existing "write-only"
  invariant was updated: `delete(point_ids, *, user_id)` verifies ownership inside the
  adapter before deleting — the caller is never trusted to pass only their own IDs.
  Qdrant verifies via `retrieve()` (fetch + payload check) then deletes only owned UUIDs.

- **Age-decay is runtime, not a batch job** (`EpisodicMemoryConfig.decay_rate = 0.05`).
  `retrieve()` multiplies each hit's score by `exp(-rate * age_days)` after fetching
  `top_k * 2` candidates, then re-sorts and re-caps. Zero writes, always current.

- **Dedup uses cosine similarity + Union-Find** (not HDBSCAN). Pairwise cosine matrix
  (numpy), edges where `sim >= similarity_threshold`, connected components via Union-Find
  with path compression. Handles transitivity correctly. No new dependencies.

- **`forget_memory` tool** — episodic counterpart to `forget_fact`. Deletes the
  `conv:{id}` point and all `moment:{id}:N` siblings for a conversation. User isolation
  enforced via `list_points` (user-scoped). Seeded into default allowlist with
  `requires_approval: true` in config.yaml — the HITL gate prevents accidental deletion.
  `recall` output was updated to include `[conversation_id]` prefix so the model has a
  handle to pass to `forget_memory`.

## Skills design decisions (agentskills.io format)

Skills are **files on disk**, never in a database. A skill is a directory containing a
`SKILL.md` file with YAML frontmatter (`name`, `description`, optional `allowed-tools`)
followed by Markdown instructions. The filesystem is the source of truth; `SkillStore`
only stores grant metadata (who can see which skills).

- **Progressive disclosure, three stages.**
  1. *Startup* (`service.build()`): `discover(cfg.skills.paths)` scans configured
     directories and loads `name` + `description` only — ~50 tokens/skill in the system
     message. Safe to run synchronously (filesystem I/O, no network).
  2. *Activation* (agent-driven): the agent calls `read_skill(name)` → full `SKILL.md`
     body returned as a tool observation. Body is read from disk on each call (no cache)
     so operators can update files without restarting the service.
  3. *Reference loading* (on demand): skill instructions can tell the agent to read
     `references/foo.md` using existing file tools — no special plumbing needed.

- **Context assembly.** The skills block is tier-0 (never evicted by the budgeter) and
  sits between the dynamic system prompt and the factual block:
  `base_prompt → dynamic → skills_block → factual → episodic → summary`.
  `ContextBuilder` receives `skill_manager` and `skill_store` as optional fields (default
  `None`); when both are absent the output is byte-for-byte identical to pre-skills
  context, so the golden test is unaffected.

- **`allowed-tools` is parsed, not auto-granted.** The field is stored in
  `SkillMeta.allowed_tools` for operator inspection. A skill definition never silently
  expands what a user can execute — `PermissionStore` remains the authorization boundary.
  Operators who want to grant a skill's tools can call `PermissionStore.grant(...)` explicitly.
  (V2 will add an opt-in `auto_grant_tools` policy, mirroring MCP's `auto_allow`.)

- **`SkillStore` Protocol: scaffolded for v2 per-user grants.**
  `allowed_skills(user_id) → None` means all skills are visible (v1 global default);
  `→ set[str]` means the user is restricted to exactly those names. `SkillManager` methods
  `metadata_block(allowed, header)` and `read_body(name, allowed)` both accept the allowed
  set and filter accordingly — no API change when a v2 `SqliteSkillStore` adapter is added.

- **`read_skill` permission seeding.** `read_skill` is a native tool that must be in the
  `PermissionStore` default allowlist so all users can call it. Seeding is done
  synchronously in `build_stores(cfg, extra_default_allowed={"read_skill"})` — no async
  `extend_default_allowed` call needed at startup.

- **Defense-in-depth.** `read_skill`'s handler re-checks `SkillStore.allowed_skills(user_id)`
  at execution time, mirroring `ToolRegistry.execute()`'s re-check of `PermissionStore`.

- **Script execution is deliberately deferred.** Skills can bundle `scripts/` directories per
  the agentskills.io spec, but the agent has no shell tool today. Adding one is a separate
  security decision (sandboxing, approval gates) tracked in ROADMAP.md.

## llm_kit gotchas (verified against the installed package)

- `TokenUsage` is **not** re-exported from top-level `llm_kit`; import from
  `llm_kit.llm.response`.
- `AppConfig.from_dict` / `from_yaml` **reject unknown keys** — that's why
  agent_kit's config nests the `llm_kit` block rather than appending sections.
- `LLMClient` and `OpenAICompatibleEmbedder` both accept `client=` and
  `owns_client=` — `service.py` builds one shared `httpx.AsyncClient` for both.

## MCP gotchas (verified against `mcp` 1.27.x)

- The `mcp` SDK is the **optional `mcp` extra**; `tools/mcp.py` imports it **lazily
  inside `connect()`** so the module loads without the extra (matches `stores/stubs.py`).
- Transport clients are async context managers with **different return arities**:
  `stdio_client` / `sse_client` yield `(read, write)`; `streamablehttp_client` yields
  `(read, write, get_session_id)`. `MCPServerClient` holds them open in an
  `AsyncExitStack` for the app's lifetime (they aren't one-shot calls).
- `ClientSession` requires `await session.initialize()` before `list_tools()` /
  `call_tool()`. A tool's `inputSchema` is already JSON Schema → drops straight into
  `ToolDefinition.parameters`.
- `call_tool` returns a `CallToolResult` with `content` (text blocks) and `isError`.
  agent_kit **raises** on `isError=True` so the registry yields `ToolResult(ok=False)`
  (tool errors are observations, not exceptions).
- MCP connect/discover is async, so it can't run in the **sync** `service.build()`;
  it runs in `AgentService.astart()` (called from the serving lifespan / examples).
  Native tools are wired in `build()`; MCP tools `register()` later in `astart()`.

## HITL tool approval

- **Config:** add `requires_approval: true` (and optionally `approval_timeout_s`,
  default 30 s) to a tool's `ToolPolicy` under `tools.definitions` in `config.yaml`.
- **Event flow:** the loop emits `ToolApprovalRequired(call_id, name, arguments,
  timeout_s)` *before* executing the tool (before `ToolCallStarted`). On approval it
  continues normally; on denial or timeout it emits `ToolResult(ok=False)` with a
  human-readable reason and feeds that reason back to the model as a tool-result
  observation, so the model can explain what happened to the user.
- **WebSocket:** the WS handler runs two concurrent coroutines via `asyncio.gather` —
  `_receive` (reads every incoming WS message) and `_run_turns` (drives the agent loop
  from a queue). Approval responses arrive as
  `{"type": "approval", "call_id": "…", "approved": true}` on the same connection;
  `_receive` routes them to `Agent.resolve_approval()`, which resolves the
  `asyncio.Future` the loop is awaiting.
- **SSE:** one-way transport — the loop's future is resolved to `False` immediately
  after the SSE handler yields the `ToolApprovalRequired` frame. The auto-deny appears
  as a normal `ToolResult(ok=False)` in the stream.
- **In-process caveat:** approval futures live in `Agent._pending_approvals` (in
  process memory, same as the rate-limiter). In a multi-worker deploy the approval
  response must reach the same worker as the running turn. WS connections are
  typically sticky, so this is safe in practice; a shared-store backing is a later
  scaling step if needed.

## Telemetry / tracing (Langfuse)

- **One seam, one import.** `telemetry.py` is the *only* module that imports
  `langfuse`. It's a leaf (like `tokens.py`/`retry.py`), so any layer may call it
  without breaking the bottom-up rule. Every call site uses `telemetry.span(...)` /
  `turn_span(...)` / `SpanHandle`, never a `langfuse` type — so switching to pure OTel
  (Langfuse v4 *is* OTel underneath) means reimplementing this one file, not
  re-instrumenting. Optional `telemetry` extra; lazy import inside `configure()`.
- **Off by default → no-op.** `TelemetryConfig.enabled` defaults false; every helper
  becomes a null context manager, so the default suite stays offline and the golden
  context test is untouched. `service.build()` only wraps the LLM/embedder in
  `TracingLLM`/`TracingEmbedder` when enabled, so `FakeLLM` runs bare in tests.
- **identity:** `conversation_id` → Langfuse **session**, `user_id` → Langfuse **user**
  (via `propagate_attributes` in `turn_span`), so a whole conversation groups in the UI.
- **Span tree:** `turn` (root) → `context.build` (+ the four source reads) → per-iteration
  `llm.invoke_stream` *generation* (model + token usage → Langfuse prices it, covering
  M9 cost) → `tool.execute:{name}` (outcome tag: ok/not_permitted/rate_limited/…).
  Background writes are spanned in `_guard`; they stay in the same trace because
  `asyncio.create_task` copies the OTel context live at `_enqueue` time. Conversation
  finalize is its own `conversation_end` root under the same session.
- **Streaming rule:** the `invoke_stream` wrapper must never buffer — it yields each
  `TextChunk` straight through (TTFT) and uses `start_observation`/`end()` (not a
  context manager held across `yield`s, which would shuffle the OTel current-span var).
- **Prometheus `/metrics`** — five instruments via `prometheus_client` (optional `metrics`
  extra): `agent_kit_ttft_seconds`, `agent_kit_turn_latency_seconds`,
  `agent_kit_turn_iterations`, `agent_kit_tool_calls_total` (labels `tool`+`outcome`),
  `agent_kit_retrieval_hits`. Same seam pattern as `telemetry.py`: single `metrics.py`
  leaf, no-op by default (`MetricsConfig.enabled=false`). `/metrics` returns 501 JSON
  when disabled, Prometheus text format when enabled.

## Running things

```bash
uv sync --extra dev --extra mcp --extra telemetry   # use --native-tls on this machine
uv run pytest                       # unit tests only, no network/Docker
ANTHROPIC_API_KEY=... uv run python examples/single_turn.py
ANTHROPIC_API_KEY=... uv run uvicorn "agent_kit.serving.app:create_app_from_yaml" --factory
```

Note: `uv` on this Windows box needs `--native-tls` or it fails with a cert error.

## Live integration tests

`tests/integration/` is an opt-in suite that exercises real LLM calls.
Skipped by default; enabled by setting `LIVE_TESTS_ENABLED=1`.

**Setup:**
1. Edit `config_live.yaml` (project root) to set your provider, model, and
   `api_key_env` (the name of the env var holding your API key).
2. Export your API key under that name (e.g. `ANTHROPIC_API_KEY=sk-...`).
3. Run: `LIVE_TESTS_ENABLED=1 uv run pytest tests/integration/ -v`

The embedder is always `FakeEmbedder` — no embed endpoint is needed.
All stores are in-memory; no external infra required beyond the LLM key.

**Coverage areas:**
- `test_streaming.py` — event sequence and token usage invariants
- `test_tool_roundtrip.py` — real LLM calls `remember_fact`, ≥2 iterations
- `test_native_memory_tools.py` — `list_facts`, `forget_fact`, `recall` invoked by real LLM
- `test_working_memory.py` — rollover fires when `buffer_token_budget` is exceeded
- `test_episodic_memory.py` — `end_conversation` writes one vector point per conversation
- `test_factual_extraction.py` — durable facts extracted; ephemeral context omitted
- `test_skills.py` — skill discovered at startup; `read_skill` called; instructions followed

## Testing posture

`FakeLLM` (in `tests/conftest.py`) replays scripted streamed turns (text chunks +
`StreamEnd` with tool calls); `make_service(cfg, turns=...)` wires it into the real
stores. Mirrors `llm_kit`'s own fake-driven posture. **No live-key tests in-repo
today** — but that will change (see ROADMAP: live integration testing is a planned,
opt-in, key-gated suite). Keep new unit tests network-free.

## When you change something

- Re-run `uv run pytest`. The golden context test (`tests/test_context.py`) asserts
  the *exact* assembled message list — if you change assembly order or block
  formatting, update it deliberately.
- Keep the layering. Keep it async. Keep it user-scoped.
- **If you change the config structure** (add/remove/rename fields in `src/agent_kit/config/schema.py`),
  update [docs/config.md](docs/config.md) with the new field(s), including type, default, and purpose.
  This keeps the operator-facing documentation in sync with the code.
- Update [ROADMAP.md](ROADMAP.md) when you complete or start a milestone.
