# agent_kit

A stateful, online, multi-turn **agentic chatbot** service built **on top of**
[`llm_kit`](https://github.com/sharma-n/llm_kit). It adds the three things `llm_kit`
deliberately omits — conversation **state**, **memory**, and a tool-calling **agent
loop** — while reusing `llm_kit`'s provider formatters, streaming `invoke_stream`,
embedder, rate limiter, retries, and error hierarchy verbatim.

Multi-user from the start: memory is per-user and tool permissions are per-user; one
global config serves all users. See [SPEC.md](SPEC.md) for the full design.

## What's here (runnable vertical slice)

A demoable chatbot end-to-end:

- **Streaming agent loop** — `invoke_stream` → typed `AgentEvent` stream
  (`TextDelta` / `ToolCallStarted` / `ToolResult` / `TurnComplete`), with a bounded
  multi-step tool loop and the SPEC §5 safety rails (`max_iterations` cap, tool
  errors as observations, per-tool timeout, optional per-turn budget).
- **Context construction + budgeter** — assembles system + factual + episodic +
  rolling-summary + working buffer in SPEC §6.2 order, under a tiered token budget.
- **Memory behind Protocols** — working (session), factual (profile), episodic
  (vectors). Ships **in-memory** reference adapters (zero infra) with real
  Redis/SQLite/Qdrant adapters dropping in behind the same Protocol.
- **Per-user tool permissions** — a `PermissionStore` gates which tools each user
  sees *and* can execute; the global config holds only the default allowlist.
- **FastAPI serving** — websocket + SSE streaming the event frames.

## Layout

```
src/agent_kit/
  config/    dataclass tree + YAML loader (nested llm_kit block)
  stores/    Protocols + in-memory adapters (session/profile/vectors/permissions)
  memory/    working / episodic / factual cognition over the stores
  tools/     registry + native tools (remember_fact, recall) + execution; MCP stub
  agent/     AgentEvent types, context builder, budgeter, the run_turn loop
  serving/   FastAPI websocket + SSE app
  service.py composition root (config → stores → memory → tools → agent)
examples/    single_turn.py (direct), ws_client.py (over the server)
tests/       pytest suite, FakeLLM-driven (no network)
config.yaml  one global config; agent_kit sections + a nested llm_kit block
```

Strict bottom-up layering: `config → stores → memory → tools → agent → serving`.

## Install & run

`uv`-managed, Python 3.13+.

```bash
uv sync --extra dev          # adds pytest + pytest-asyncio + httpx
uv run pytest                # 25 tests, no network/Docker needed
```

Drive a conversation directly (set `OPENAI_API_KEY`, or point the nested `llm_kit`
block in `config.yaml` at any OpenAI/Anthropic/Gemini-compatible endpoint):

```bash
OPENAI_API_KEY=... uv run python examples/single_turn.py
```

Or run the server and connect the websocket client:

```bash
OPENAI_API_KEY=... uv run uvicorn "agent_kit.serving.app:create_app_from_yaml" --factory
uv run python examples/ws_client.py
```

## Configuration

One `config.yaml`: agent_kit sections (`agent`, `memory`, `context`, `stores`,
`mcp`, `tools`) plus a nested `llm_kit:` block handed wholesale to `llm_kit`'s
`AppConfig`. Supports `${VAR}` / `${VAR:-default}` interpolation. Flip a
`stores.*_backend` to swap the in-memory adapter for a real one.

## Deferred (next milestones, scaffolded behind Protocols)

Real Redis/SQLite/Qdrant adapters · MCP multi-server discovery/execution · episodic
& factual async write paths (currently fire-and-forget enqueue) · rolling-summary
summarizer activation · offline batch consolidation · observability + cost
accounting via `UsageLedger`.

## License

Add your own.
