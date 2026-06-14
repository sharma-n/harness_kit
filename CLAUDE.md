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
config → stores → memory → tools → agent → serving
```

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
  stores/      base.py (4 Protocols) · types.py (records) · memory_*.py (in-memory adapters)
               · stubs.py (real adapters, NotImplementedError) · factory.py (backend select)
  memory/      working.py (buffer + token-budget rollover) · episodic.py (conversation-end
               embed) · factual.py   (cognition over the stores)
  tools/       base.py (Tool) · registry.py (user-scoped exec + per-tool policy) · native.py
               · ratelimit.py (in-process per-user token bucket) · mcp.py (MCPServerClient
               connect/discover + MCPManager aggregate)
  agent/       events.py (AgentEvent) · context.py (assembly §6.2) · budgeter.py (tiers §6.5)
               · loop.py (run_turn §5 + end_conversation)
  serving/     wire.py (AgentEvent→frame) · app.py (FastAPI ws + sse)
  service.py   composition root: config → stores → memory → tools → agent
  llm.py       LLM / Embedder Protocols over llm_kit
  tokens.py    estimate_tokens — leaf estimator shared by memory/ rollover + agent/ budgeter
  retry.py     retry_async / store_write — exp backoff + jitter for store-write retries
  errors.py    AgentKitError hierarchy (reuse llm_kit.LLMError for provider failures)
examples/      single_turn.py (direct) · ws_client.py (over server)
tests/         conftest.py (FakeLLM/FakeEmbedder + make_service) + per-layer tests
config.yaml    one global config; agent_kit sections + nested llm_kit block
```

## Key abstractions to know

- **`AgentEvent`** (`agent/events.py`): `TextDelta | ToolCallStarted | ToolResult |
  TurnComplete`. `run_turn` yields these; `serving/wire.py` encodes them to JSON
  frames. This is the load-bearing abstraction — a streaming tool loop can't yield
  bare tokens.
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
  remaining buffer are embedded as ONE point — cheaper and more compact than per-turn,
  trading per-turn recall precision for conversation-level memory. The point id is
  deterministic (`conv:{conversation_id}`) so re-finalizing a resumed conversation
  upserts rather than duplicating. If finer recall is later needed, revisit to
  per-N-turns/hybrid.

- **Conversation end is a two-stage idle lifecycle, not a single TTL** (config
  validates `idle_finalize_s < ttl_s`). `idle_finalize_s` fires first: the conversation
  is embedded but the session stays loadable so a returning user resumes seamlessly;
  `ttl_s` then evicts. `end_conversation` is best-effort and **idempotent** — missing/
  expired session or non-owner caller → no-op; `SessionState.finalized_at` (cleared on
  any new activity) stops re-embedding until the conversation is resumed. It is driven
  from two places: **WebSocket disconnect** in `serving/app.py` (fast path) and a
  **background idle sweeper** (`Agent.sweep_idle`, started in the serving lifespan,
  cadence `sweep_interval_s`). The sweeper is what gives **SSE** — which has no
  disconnect signal — a conversation-end event, and also catches abrupt WS drops.

- **Background writes are fire-and-forget with logging + retry** — `extract`, `maybe_rollover`,
  `mark_finalized`, and `write_conversation` are enqueued via `Agent._enqueue()` and run
  off the hot path. A failure is no longer silent: `_guard()` logs one ERROR with
  operation + `user_id` + `conversation_id`; `sweep_idle` logs per-conversation WARNING
  and continues (no cascade). Store-write retries (via `retry.store_write()`) wrap **only**
  the store call, not the preceding LLM/embedder step — that's already retried by llm_kit,
  so a transient store fault never re-runs the model. Tunable via `MemoryConfig.store_retry`.
  All background store ops are verified idempotent (except append-only `append_turn`).

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

## Running things

```bash
uv sync --extra dev --extra mcp     # use --native-tls on this machine
uv run pytest                       # 72 tests, no network/Docker
OPENAI_API_KEY=... uv run python examples/single_turn.py
OPENAI_API_KEY=... uv run uvicorn "agent_kit.serving.app:create_app_from_yaml" --factory
```

Note: `uv` on this Windows box needs `--native-tls` or it fails with a cert error.

### Known environment caveat (live path)

On this machine, instantiating *any* `httpx.AsyncClient` crashes with
`OPENSSL_Uplink ... no OPENSSL_Applink` — a local httpx/OpenSSL FFI issue,
independent of agent_kit and llm_kit. It blocks only the **live-network path**; all
logic is exercised via the `FakeLLM` suite. On a healthy httpx/OpenSSL install the
examples and server run as documented.

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
- Update [ROADMAP.md](ROADMAP.md) when you complete or start a milestone.
