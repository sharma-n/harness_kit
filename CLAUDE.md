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
backing, that breaks horizontal scaling (SPEC §12) — don't.

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
  memory/      working.py · episodic.py · factual.py   (cognition over the stores)
  tools/       base.py (Tool) · registry.py (user-scoped exec) · native.py · mcp.py (stub)
  agent/       events.py (AgentEvent) · context.py (assembly §6.2) · budgeter.py (tiers §6.5)
               · loop.py (run_turn §5)
  serving/     wire.py (AgentEvent→frame) · app.py (FastAPI ws + sse)
  service.py   composition root: config → stores → memory → tools → agent
  llm.py       LLM / Embedder Protocols over llm_kit
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

## llm_kit gotchas (verified against the installed package)

- `TokenUsage` is **not** re-exported from top-level `llm_kit`; import from
  `llm_kit.llm.response`.
- `AppConfig.from_dict` / `from_yaml` **reject unknown keys** — that's why
  agent_kit's config nests the `llm_kit` block rather than appending sections.
- `LLMClient` and `OpenAICompatibleEmbedder` both accept `client=` and
  `owns_client=` — `service.py` builds one shared `httpx.AsyncClient` for both.

## Running things

```bash
uv sync --extra dev                 # use --native-tls on this machine
uv run pytest                       # 25 tests, no network/Docker
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
