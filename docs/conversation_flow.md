# Conversation Flow

This document describes what happens when a user sends a message, and where the relevant code lives.

## Sequence Diagram

```mermaid
sequenceDiagram
    actor User
    participant Transport as serving/app.py<br/>(WebSocket / SSE)
    participant Agent as agent/loop.py<br/>Agent.run_turn()
    participant CtxBuilder as agent/context.py<br/>ContextBuilder.build()
    participant Working as memory/working.py<br/>WorkingMemory
    participant Episodic as memory/episodic.py<br/>EpisodicMemory
    participant Factual as memory/factual.py<br/>FactualMemory
    participant Registry as tools/registry.py<br/>ToolRegistry
    participant LLM as llm.py / llm_kit<br/>invoke_stream()

    rect rgb(235, 245, 235)
        note over Transport,Registry: Startup (once) — serving lifespan
        Transport->>Registry: astart(): connect MCP servers, register discovered tools<br/>(namespaced {server}__{tool}; auto_allow → default allowlist)
    end

    User->>Transport: send message (user_id, conversation_id, text)

    Transport->>Agent: run_turn(user_id, conversation_id, message)

    rect rgb(230, 240, 255)
        note over Agent,Registry: Context Assembly (every turn)
        Agent->>CtxBuilder: build(user_id, conversation_id, message)
        CtxBuilder->>Working: load(conversation_id, user_id)
        Working-->>CtxBuilder: buffer (recent turns) + rolling summary
        CtxBuilder->>Factual: get(user_id)
        Factual-->>CtxBuilder: user profile / facts
        CtxBuilder->>Episodic: retrieve(user_id, message, buffer)
        Episodic-->>CtxBuilder: top-k memory hits (vector search)
        CtxBuilder->>Registry: definitions(user_id)
        Registry-->>CtxBuilder: allowed tool definitions
        CtxBuilder-->>Agent: AssembledContext (messages[], tools[])
    end

    rect rgb(230, 255, 230)
        note over Agent,LLM: Agent Loop (up to max_iterations)
        loop until no tool_calls or max_iterations
            Agent->>LLM: invoke_stream(messages, tools)
            LLM-->>Transport: TextDelta (streamed text chunks)
            LLM-->>Agent: StreamEnd (response + tool_calls)

            alt model requested tool calls
                loop for each tool_call
                    Agent-->>Transport: ToolCallStarted event
                    Agent->>Registry: execute(user_id, call)
                    Registry-->>Agent: Execution (ok, observation)
                    Agent-->>Transport: ToolResult event
                    Agent->>Agent: append tool result to messages[]
                end
            else no tool calls
                Agent->>Agent: break loop (stop_reason=completed)
            end
        end
    end

    rect rgb(255, 240, 220)
        note over Agent,Episodic: Persist (after loop, off hot path)
        Agent->>Working: append_turn(user turn + assistant turn)
        Agent-->>Factual: extract(user_id, exchange) [background task]
        Agent-->>Working: maybe_rollover() [background; summarize oldest if over token budget]
    end

    Agent-->>Transport: TurnComplete (usage, iterations, stop_reason)
    Transport-->>User: stream of encoded JSON frames

    rect rgb(245, 235, 255)
        note over Transport,Episodic: Conversation end — WS disconnect OR background idle sweeper (covers SSE)
        Transport->>Agent: end_conversation(user_id, conversation_id)  [on WS disconnect]
        Agent->>Agent: sweep_idle(idle_finalize_s)  [periodic; finalizes idle conversations]
        Agent->>Working: peek(summary + remaining buffer)
        Agent-->>Episodic: write_conversation() — embed ONE point for the whole conversation
        Agent->>Working: mark_finalized() — idempotent until new activity
    end
```

## Stage Breakdown

### 0. Startup (once, at app boot) — `serving/app.py` lifespan

Before any turn is served, the FastAPI **lifespan** calls `AgentService.astart()`, which connects every configured MCP server (`mcp.servers` in `config.yaml`) via `tools/mcp.py` (`MCPManager` → `MCPServerClient`), discovers each server's tools, and **registers** them into the same `ToolRegistry` the native tools live in — namespaced `{server}__{tool}`. This is best-effort: a server that fails to connect within `mcp.startup_timeout_s` is logged and skipped, never blocking startup. A trusted server marked `auto_allow: true` has its discovered tools folded into the global default allowlist here. The lifespan also starts the idle sweeper and, on shutdown, calls `AgentService.aclose()` to drain background writes and close MCP + HTTP connections.

The upshot for the per-turn flow below: by the time a message arrives, MCP tools are just ordinary entries in the registry — indistinguishable from native tools at every stage that follows.

### 1. Entry Point — `serving/app.py`

The client connects via **WebSocket** (`/ws/{conversation_id}`) or **SSE** (`/sse/{conversation_id}`). Both transports parse `user_id` and `message` from the client payload and delegate to `Agent.run_turn()`. Events yielded by the loop are encoded to JSON frames by `serving/wire.py` and streamed back as they arrive — `TextDelta`s are forwarded immediately, so the user sees text before the turn completes.

### 2. Context Assembly — `agent/context.py`

Before the first LLM call, `ContextBuilder.build()` fetches and assembles five sources, all scoped to the calling `user_id`:

| Source | Store | What it adds |
|---|---|---|
| Working buffer | `SessionStore` | Recent turns verbatim (oldest → newest) |
| Rolling summary | `SessionStore` | Compressed summary of earlier turns |
| Factual profile | `ProfileStore` | Known facts about this user |
| Episodic hits | `VectorStore` | Semantically relevant past memories |
| Tool definitions | `PermissionStore` | Only the tools this user is allowed to see (native **and** MCP-discovered, filtered by the per-user allowlist) |

The assembled message list follows the order defined in SPEC §6.2:
```
[system: identity + factual + episodic + summary]
[user/assistant ... working buffer ...]
[user: current message]
```

Token budget allocation and eviction happen here via `agent/budgeter.py` before assembly.

### 3. Agent Loop — `agent/loop.py`

`run_turn()` drives the tool-calling loop up to `max_iterations`:

1. Call `invoke_stream()` on the LLM, yielding `TextDelta` events as text arrives.
2. On `StreamEnd`, inspect `response.tool_calls`.
3. If tool calls are present: emit `ToolCallStarted`, execute via `ToolRegistry`, emit `ToolResult`, append the observation to `messages[]`, and loop back to step 1.
4. If no tool calls (or iteration cap hit): break.

Tool errors are **observations, not exceptions** — a failed, denied, or timed-out tool becomes a `ToolResult(ok=False)` fed back to the model rather than crashing the turn.

### 4. Tool Execution — `tools/registry.py`

`ToolRegistry.execute()` enforces a two-layer permission check (definition-time filter + execute-time re-check), applies a per-tool timeout, and wraps every failure mode into an `Execution(ok=False)`.

Native and MCP tools execute through the **same** path. A native tool runs its in-process handler; an MCP tool's handler forwards the call to its server's `MCPServerClient.call()` (`tools/mcp.py`), which invokes the remote/subprocess tool and joins the result's text. An MCP server returning `isError` is raised inside the handler so it lands as a `ToolResult(ok=False)` observation — same as any other tool failure. The per-tool timeout covers the round-trip to the MCP server too.

### 5. Persist — `agent/loop.py` (`_persist`)

After the loop completes, the turn is written to memory in two tiers:

- **Hot path (synchronous):** `WorkingMemory.append_turn()` writes both the user and assistant turns to the session buffer immediately.
- **Off hot path (background `asyncio.Task`s):** factual extraction and `WorkingMemory.maybe_rollover()` run as fire-and-forget tasks — they do not block the response stream. Rollover is **token-budget-driven**: when the verbatim buffer exceeds `buffer_token_budget`, the oldest turns are summarized (LLM `invoke` + `response_model`) into the rolling summary and dropped from the buffer.

Note: episodic embedding does **not** happen here. It is deferred to conversation end (Stage 6).

Finally, `TurnComplete` is yielded with token usage, iteration count, and stop reason.

### 6. Conversation end — `agent/loop.py` (`end_conversation`, `sweep_idle`)

When a conversation ends, `Agent.end_conversation()` reads the rolling summary + remaining buffer and embeds the **whole conversation as a single episodic point** via `EpisodicMemory.write_conversation()`. Embedding once per conversation (rather than once per turn) keeps the vector store compact and embedding cost low, trading per-turn recall precision for conversation-level memory. It is best-effort and **idempotent**: a missing/expired session or non-owner caller is a no-op, and `SessionState.finalized_at` stops it re-embedding until new activity.

**Two-stage idle lifecycle** (config validates `idle_finalize_s < ttl_s`):

| Timer | What fires | Effect |
|---|---|---|
| `idle_finalize_s` (e.g. 15 min) | `end_conversation()` | Embeds the conversation; **session is kept** so the user can resume seamlessly |
| `ttl_s` (e.g. 60 min) | session-store eviction | Session removed; already finalized, so no memory is lost |

`end_conversation()` is reached two ways:

- **WebSocket disconnect** (fast path): `serving/app.py` calls it when the socket closes.
- **Background idle sweeper** (`Agent.sweep_idle`, started in the serving **lifespan**, scanning every `sweep_interval_s`): finalizes any conversation idle past `idle_finalize_s`. This is the only conversation-end signal **SSE** gets — SSE is one-directional and never reports a disconnect — and it also catches abrupt WS drops that never fire their handler.

**Resuming after finalize:** if the user returns before `ttl_s` eviction, the session is still there with full history, so the conversation simply continues; appending a turn clears `finalized_at`, so it will be finalized again later. Because the episodic point id is deterministic per conversation, that second finalize **upserts** the single point rather than creating a duplicate. If the user returns only after `ttl_s` eviction, a fresh working buffer starts, but the earlier conversation is still recallable via episodic search.

## Code Map

| Stage | File |
|---|---|
| Startup wiring (composition root, `astart`/`aclose`) | `src/harness_kit/service.py` |
| Entry point (WS/SSE) + lifespan (MCP connect, idle sweep) | `src/harness_kit/serving/app.py` |
| Agent loop | `src/harness_kit/agent/loop.py` |
| Context assembly | `src/harness_kit/agent/context.py` |
| Token budget / eviction | `src/harness_kit/agent/budgeter.py` |
| Working memory (buffer + token-budget rollover) | `src/harness_kit/memory/working.py` |
| Episodic memory (vector; conversation-end embed) | `src/harness_kit/memory/episodic.py` |
| Token estimator (shared) | `src/harness_kit/tokens.py` |
| Factual memory (profile) | `src/harness_kit/memory/factual.py` |
| Tool execution + authz | `src/harness_kit/tools/registry.py` |
| Native tools (`remember_fact` / `forget_fact` / `list_facts` / `recall`) | `src/harness_kit/tools/native.py` |
| MCP client (connect / discover / invoke external servers) | `src/harness_kit/tools/mcp.py` |
| Event types streamed out | `src/harness_kit/agent/events.py` |
| Wire encoding to JSON | `src/harness_kit/serving/wire.py` |
