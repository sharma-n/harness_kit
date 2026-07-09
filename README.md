# harness_kit

A production-shaped, **multi-tenant agentic chatbot service** — streaming, stateful,
multi-turn, with memory, tool use, and per-user isolation baked in at every layer.

Use this when you need to serve many users from one deployment and you don't want to
retrofit multi-tenancy later. Every design decision — memory scoping, tool permissions,
session ownership, context budgeting — assumes multiple users from the start.

---

## When to reach for this

| You need | harness_kit gives you |
|---|---|
| Persistent, resumable conversations per user | Session store with two-stage TTL (finalize → evict) |
| Memory that survives across conversations | Episodic (vector) + factual (profile) stores, per-user |
| Tools with different trust levels per user | `PermissionStore` gates definitions *and* execution |
| Streaming with mid-turn tool calls | Typed `AgentEvent` stream (`TextDelta / ToolCallStarted / ToolResult / TurnComplete`) |
| Hard limits on runaway agents | `max_iterations`, per-tool timeout, per-turn wall-clock budget |
| Any model — cloud or local | One config change: point at OpenAI, Anthropic, Gemini, LM Studio, Ollama, or any OpenAI-compatible endpoint |
| Zero infra locally, real backends in prod | Swap in Redis/SQLite/Qdrant by flipping one config key |
| Traces + cost accounting per conversation | Langfuse spans on every LLM call, tool call, and background write |

**Not the right fit if** you're doing stateless single-turn inference, batch processing,
or building for a single user. The complexity pays off at multi-user scale.

---

## What's included

**Streaming agent loop** — each turn runs `invoke_stream` in a bounded tool loop.
Tool errors are fed back to the model as observations, never raised as exceptions.
The loop stops at `max_iterations` or an optional per-turn time budget.

**Five-source context assembly, tiered budget** — every prompt is built from five
sources in a fixed priority order: system prompt → factual profile → episodic memories
→ rolling summary → working buffer → current message. A tiered eviction policy keeps
the prompt under the model's context window: tier-0 (system, current message, tool
definitions) never drops; older working-buffer turns evict first; episodic hits drop
by score.

**Three memory stores**
- *Working* — recent turns + a rolling summary. When the buffer exceeds a token budget,
  the oldest turns are folded into the summary off the hot path.
- *Episodic* — the whole conversation is embedded as one vector point at conversation
  end, recalled by semantic similarity in future conversations. Optionally, the LLM also
  flags 1–N notable discussion threads and embeds each as a sibling point, improving
  recall precision for specific topics without per-turn embedding noise
  (`flagged_moments_enabled` in config).
- *Factual* — a structured key-value user profile (occupation, preferences, habits,
  constraints, and any other timeless user attribute). Updated by explicit tool calls
  (`remember_fact` to add/update, `forget_fact` to delete, `list_facts` to read) or by
  automatic extraction after each turn.

**Per-user tool permissions** — a `PermissionStore` gates which tools each user can
see *and* execute (checked twice: at definition time and again at execution — defense
in depth). The global config holds only defaults; grants are per-user in the store.

**MCP support** — connect multiple MCP servers; tools are namespaced `{server}__{tool}`.
A server that fails to start is logged and skipped; one bad server never crashes the service.

**FastAPI serving** — WebSocket and SSE, both streaming the same `AgentEvent` frames.
Conversation finalization is triggered by WebSocket disconnect (fast path) or a
background idle sweeper that also covers SSE and abrupt drops.

**Observability** — Langfuse tracing (optional, off by default) with a span tree
covering context assembly, every LLM generation, every tool call, and background
writes. Prometheus metrics also available. Swapping to pure OTel means editing one
file (`telemetry.py`), not re-instrumenting call sites.

---

## Quickstart

Python 3.13+, managed with `uv`.

```bash
# Install with dev dependencies
uv sync --extra dev

# Run the test suite (no network, no Docker needed)
uv run pytest
```

Run a single conversation directly against the LLM:

```bash
OPENAI_API_KEY=sk-... uv run python examples/single_turn.py
```

Or start the server and connect the WebSocket client:

```bash
OPENAI_API_KEY=sk-... uv run uvicorn "harness_kit.serving.app:create_app_from_yaml" --factory
# in another terminal:
uv run python examples/ws_client.py
```

Works with any OpenAI-compatible endpoint — Anthropic, Gemini, local models. Set the
provider in `config.yaml` under the `llm_kit:` block.

---

## Configuration

One `config.yaml` for everything. Supports `${VAR}` / `${VAR:-default}` interpolation.

```yaml
agent:
  max_iterations: 6           # hard cap on tool-call rounds per turn
  per_turn_budget_s: 30       # optional wall-clock limit per turn

memory:
  buffer_token_budget: 4000   # working buffer before rollover kicks in
  idle_finalize_s: 900        # embed conversation after this idle period
  ttl_s: 3600                 # evict session after this total idle

stores:
  session_backend: memory     # swap to: redis
  profile_backend: memory     # swap to: sqlite
  vector_backend: memory      # swap to: qdrant

llm_kit:                      # passed wholesale to llm_kit
  provider: openai
  model: gpt-4o
  api_key: ${OPENAI_API_KEY}
```

Flipping `*_backend` from `memory` to a real backend requires no code change — the
stores implement a shared Protocol.

---

## Opinionated choices

These aren't configurable — they're the point of the library:

- **Multi-user isolation is enforced at the store boundary.** `SessionStore.load`
  raises `UnauthorizedError` on cross-user access. Vector search always filters by
  `user_id`. No layer may cache mutable per-user state in-process without a
  shared-store backing (one documented exception: the in-process tool rate limiter,
  which mirrors llm_kit's own).

- **Everything is async.** A synchronous call on the event loop stalls every concurrent
  conversation. The in-memory stores are async too, so real backends drop in behind the
  same Protocol.

- **Strict bottom-up layering.** `config → stores → memory → tools → agent → serving`.
  No layer imports from above it. `service.py` is the only composition root.

- **Tool errors are observations.** A failed, timed-out, or permission-denied tool
  call becomes `ToolResult(ok=False)` fed back to the model. The only things that
  raise are `max_iterations` and `UnauthorizedError`.

- **Episodic embedding is per-conversation, not per-turn.** The whole conversation
  (summary + remaining buffer) is embedded as one vector point at conversation end.
  Cheaper and more compact; trades per-turn recall precision for embedding cost. An
  optional `flagged_moments_enabled` mode adds focused sibling points for notable
  discussion threads — the balance between a single broad blob and per-turn noise.

- **Rollover is token-budget-driven, not turn-count-driven.** The trigger is the
  estimated token size of the working buffer, not a fixed number of turns.

- **Background writes are fire-and-forget with retry.** Rollover, fact extraction,
  and episodic writes are enqueued off the hot path. Failures are logged with
  full context (operation, user_id, conversation_id) and retried with backoff —
  but a store fault never re-runs the model.

---

## Layout

```
src/harness_kit/
  config/    schema.py (dataclass tree) + loader.py (YAML + ${VAR})
  stores/    4 Protocols + in-memory adapters + real-backend stubs + factory
  memory/    working.py · episodic.py · factual.py
  tools/     base.py · registry.py · native.py · ratelimit.py · mcp.py
  agent/     events.py · context.py · budgeter.py · loop.py
  serving/   wire.py (AgentEvent → JSON frames) · app.py (FastAPI WS + SSE)
  service.py composition root
  llm.py     LLM / Embedder Protocols (thin wrapper; lets FakeLLM drive tests)
  tokens.py  estimate_tokens — shared leaf used by memory/ and agent/
  telemetry.py  one file, one langfuse import
examples/   single_turn.py · ws_client.py
tests/      FakeLLM-driven, no network required
config.yaml one config to rule them all
```

Current status and roadmap: [ROADMAP.md](ROADMAP.md).

---

## Built on

[`llm_kit`](https://github.com/sharma-n/llm_kit) — provider wire formats, streaming
`invoke_stream`, structured `invoke`, embedder, rate limiting, retries, and the error
hierarchy. `harness_kit` adds state, memory, and the tool loop on top; it does not
reimplement anything `llm_kit` already owns.

---

## License

Add your own.
