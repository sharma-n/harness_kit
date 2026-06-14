# SPEC — `agent_kit` (working name)

A stateful, online, multi-turn **agentic chatbot** service built **on top of**
[`llm_kit`](https://github.com/) as a dependency (`uv add llm_kit`). It adds the
three things `llm_kit` deliberately does *not* have — conversation state, memory,
and a tool-calling agent loop — while reusing `llm_kit`'s provider formatters,
streaming invoke, embedder, rate limiter, retry, and error hierarchy verbatim.

> **Design assumptions baked into this spec**
> - `llm_kit` already exposes a single-call streaming API (`invoke_stream`) whose
>   stream surfaces both text deltas **and** partial tool-call fragments, parsed
>   per-provider inside the formatter layer.
> - `llm_kit` is installable via `uv add llm_kit` and importable as `import llm_kit`.

---

## 1. Identity, goals, non-goals

`agent_kit` is the **"identity fork"** that `llm_kit`'s ROADMAP quarantined on
purpose. It optimizes for the *opposite* of `llm_kit`:

| | `llm_kit` | `agent_kit` |
|---|---|---|
| Lifecycle | process a stream, exit | long-lived service holding sessions |
| Optimize for | throughput (items/sec) | latency (time-to-first-token) |
| State | stateless by design | stateful per user/conversation |
| Memory footprint | O(buffer), not O(N) | O(active sessions) |
| Failure unit | one batch item, isolated | one user turn in a multi-step loop |

### Goals
- Multi-turn conversations with **token streaming** to the client.
- **Memory**: working (this conversation), episodic (retrieval over a vector DB),
  and factual (a structured per-user profile — the `user.json` payload idea).
- **Tool calling via MCP**, executed in a bounded **multi-step agent loop**.
- "Prototype, but not a toy": real, swappable persistence behind Protocols.

### Non-goals (for now)
- High request concurrency. Target is **dozens of concurrent conversations** in a
  single async process; horizontal scaling is a later concern but the design must
  not preclude it (no un-shareable in-process state behind a Protocol).
- Re-implementing anything `llm_kit` already owns (provider wire formats, retries,
  rate limiting, embeddings, structured output).
- Being a general framework. Same discipline as `llm_kit`: a narrow, owned core.

### The one inherited discipline
**Strict bottom-up layering** — each layer depends only on layers below it. This
is the single property that keeps an agent codebase from becoming spaghetti, and
it is non-negotiable here.

---

## 2. Relationship to `llm_kit`

**Reused as a dependency (do not reimplement):**
- `LLMClient.invoke_stream` — streaming single calls; the engine of every turn.
- `LLMClient.invoke` + `response_model` — non-streaming structured calls, used for
  fact extraction and summarization.
- `OpenAICompatibleEmbedder` — embeds episodic writes and retrieval queries.
- Provider formatters (`message_format`: openai / anthropic / gemini), `rate_limit/`,
  `http/session`, `http/retry`, and the full error hierarchy.
- The **batch / provider_batch** pipelines — *not* on the hot path, used for
  **offline** background jobs (nightly memory consolidation, bulk re-embedding a
  knowledge base, eval runs over conversation logs).

**Built new in this repo (the actual project):** `stores/`, `memory/`, `tools/`,
`agent/`, `serving/`. See §3.

---

## 3. Architecture & layering

```
llm_kit  (invoke_stream, invoke+response_model, embedder, formatters, rate_limit, errors)
   │
config/      app config — reuse llm_kit's dataclass + YAML loader pattern
   │
stores/      persistence adapters, each behind a Protocol
   ├── session   → Redis     (hot working state, TTL'd)
   ├── profile   → SQLite     (factual memory; SQLAlchemy + aiosqlite)
   └── vectors   → Qdrant     (episodic memory)
   │
memory/      cognition over the stores
   ├── working    recent-turn buffer + rolling summary
   ├── episodic   retriever (embed query → search) + writer (embed turn → upsert)
   └── factual    profile read + fact extraction/update
   │
tools/       MCP client + tool registry + execution (+ native memory tools)
   │
agent/       the loop: context assembly → invoke_stream → tool exec → repeat
   │          emits a typed AgentEvent stream
   │
serving/     FastAPI ASGI app; websocket/SSE per session; auth stub
```

Everything is **async end-to-end**. `llm_kit` is async; Redis (`redis-py`),
SQLite (`aiosqlite`), Qdrant (`qdrant-client[async]`), and MCP clients all have
async APIs. **A synchronous DB call on the event loop is a bug** — it stalls every
concurrent conversation.

---

## 4. Core abstractions

### 4.1 The agent event stream (the load-bearing abstraction)

A multi-step tool loop *with* streaming cannot yield bare tokens: when the model
emits a tool call mid-response, the loop must pause, run the tool, and resume —
possibly several times per turn. So the agent yields **typed events**, and
`serving/` translates them to wire frames.

```python
@dataclass
class TextDelta:        # forward to the user immediately
    text: str

@dataclass
class ToolCallStarted:  # UI hint: "calling search_web(...)"
    call_id: str
    name: str
    arguments: dict

@dataclass
class ToolResult:       # optional UI trace of the observation
    call_id: str
    name: str
    ok: bool
    content: str        # truncated for display; full text fed back to the model

@dataclass
class TurnComplete:     # terminal: usage, stop reason, iteration count
    usage: TokenUsage   # reuse llm_kit's TokenUsage
    iterations: int
    stop_reason: str

AgentEvent = TextDelta | ToolCallStarted | ToolResult | TurnComplete
```

`agent.run_turn(...)` returns `AsyncIterator[AgentEvent]`.

### 4.2 Store Protocols

Stores sit behind Protocols so the prototype substrates (Qdrant/SQLite/Redis) can
be swapped without touching `memory/` or above.

```python
class SessionStore(Protocol):
    async def load(self, conversation_id: str) -> SessionState | None: ...
    async def save(self, conversation_id: str, state: SessionState) -> None: ...
    async def append_turn(self, conversation_id: str, turn: Turn) -> None: ...

class ProfileStore(Protocol):                         # factual memory
    async def get(self, user_id: str) -> UserProfile: ...
    async def upsert_facts(self, user_id: str, facts: dict) -> None: ...

class VectorStore(Protocol):                          # episodic memory
    async def add(self, points: list[MemoryPoint]) -> None: ...
    async def search(self, user_id: str, query_vector: list[float],
                     k: int, min_score: float) -> list[MemoryHit]: ...
```

---

## 5. The agent loop

```
run_turn(user_id, conversation_id, user_message) -> AsyncIterator[AgentEvent]:
    ctx = build_context(user_id, conversation_id, user_message)   # see §6
    messages = ctx.messages
    for iteration in range(max_iterations):
        tool_calls = []
        async for delta in llm_kit.invoke_stream(messages, tools=ctx.tools):
            if delta.is_text:        yield TextDelta(delta.text)
            if delta.is_tool_call:   accumulate into tool_calls
        if not tool_calls:
            break                                       # model answered
        append assistant message (with tool_calls) to messages
        for call in tool_calls:
            yield ToolCallStarted(...)
            result = await tools.execute(call)          # per-tool timeout
            yield ToolResult(...)
            append tool-result (observation) message to messages
    persist_turn(...)                                   # §7 write paths
    enqueue async memory writes
    yield TurnComplete(...)
```

**Safety rails (inherited from `llm_kit` invariant #7):**
- **`max_iterations` cap** — hard ceiling on tool round-trips per turn; on hit,
  stop and return a graceful "couldn't complete" rather than loop forever.
- **Tool errors are observations, not exceptions** — a failed/timed-out tool
  returns `ToolResult(ok=False, content=<error>)` that is fed back to the model so
  it can recover, exactly as the batch processor turns per-item failures into
  `BatchResult(error=...)` instead of crashing the run.
- **Per-tool timeout** and an optional per-turn wall-clock budget.

---

## 6. Context construction (what the model sees every step)

This is the heart of the system. Two scopes:
- **Per turn** — assembled once when a user message arrives.
- **Per loop iteration** — the per-turn context, *plus* the assistant tool calls
  and tool observations accumulated so far this turn (appended, never re-fetched).

### 6.1 The five sources

Every turn's context is assembled from five sources, three of which are memory:

| # | Source | Store | Scope | Freshness |
|---|--------|-------|-------|-----------|
| 1 | **System prompt** | static / config | global | constant |
| 2 | **Factual memory** (user profile) | SQLite | per user | read each turn |
| 3 | **Episodic memory** (retrieved) | Qdrant | per user, query-relevant | retrieved each turn |
| 4 | **Working memory** (recent turns + rolling summary) | Redis | per conversation | updated each turn |
| 5 | **Current user message** | — | this turn | this turn |

Tool definitions (6.3) ride alongside in the provider's native tool slot.

### 6.2 Assembly order (what goes where in the prompt)

Position matters for model attention. The assembled message list is:

```
[ system ]      ← 1. agent identity + behavioral rules
                ↳ 2. FACTUAL block: "What you know about this user: { … profile … }"
                ↳ 3. EPISODIC block: "Relevant memories from past conversations:
                       - (2026-02-10) user mentioned they manage a team of 6
                       - …"                              (only if hits clear threshold)
                ↳ 4a. SUMMARY block: "Summary of earlier in this conversation: …"
[ user ]        ← 4b. working-buffer turn  (oldest retained)
[ assistant ]   ← 4b. working-buffer turn
   … last N turns verbatim …
[ user ]        ← 5. the current user message
```

Rationale:
- **Factual** is small, stable, and high-value → pin it into the system block.
- **Episodic** is "long-term recall," framed as memories, *above* the live
  transcript so the model reads it as background, not as something the user just
  said.
- **Working buffer** is the literal recent transcript as role-tagged messages —
  this is the conversation.
- **Rolling summary** stands in for turns that have aged out of the buffer, so the
  conversation stays coherent past N turns without unbounded growth.

### 6.3 Tools in context

The tool registry (MCP-discovered + native) is rendered into the provider's tool
schema by `llm_kit`'s formatter. Tool **definitions** are sent every iteration;
tool **results** are appended as observation messages within the loop (§5). Keep
the active tool set scoped — passing 100 MCP tools every turn burns tokens and
degrades selection; prefer a curated/relevant subset.

### 6.4 Episodic retrieval — how the query is built

Retrieval quality hinges on the query, and raw last-message retrieval breaks on
follow-ups ("what about that one?"). Strategy:

1. **Default:** embed the current user message.
2. **Context-augmented:** prepend the last 1–2 turns to the query text before
   embedding, so pronouns/ellipsis resolve.
3. **Optional query rewrite (enhancement):** a cheap `invoke` + `response_model`
   call that rewrites the follow-up into a standalone query before embedding.
   Off by default (adds latency); enable per-deployment.

Search is **filtered by `user_id`** (no cross-user leakage), returns top-`k` with
a **`min_score` threshold** — below threshold, inject *nothing* rather than noise.

### 6.5 The context budgeter

All five sources compete for a finite input-token budget. A budgeter allocates
and truncates by priority tier; never silently overflow the model's window.

| Tier | Sources | Eviction policy when over budget |
|------|---------|----------------------------------|
| 0 — never drop | system prompt, current user message, tool defs, in-turn observations | hard-required; if these alone overflow, error |
| 1 — high | factual profile | compact (drop low-priority fields) before dropping |
| 2 — medium | working buffer (recent turns) | evict **oldest** turns first; evicted turns roll into the summary |
| 3 — medium | rolling summary | re-summarize tighter if too long |
| 4 — low | episodic hits | drop lowest-scoring first; already threshold-gated |

Budget = model context window − `max_output_tokens` − safety margin. Use
`llm_kit`'s token estimator (or a real tokenizer if supplied) to measure.

### 6.6 Worked example of one turn's context

User (turn 9) says *"Can you book the same flight as last week?"*

1. **System**: agent rules + factual block (`{name, timezone: PST, prefers: aisle seat}`).
2. **Episodic** (query = augmented "book the same flight as last week"): top-3 hits
   over threshold → e.g. *"(2026-06-06) booked SFO→JFK, UA 4567, aisle."*
3. **Working**: last 6 turns verbatim; turns 1–2 represented by the rolling summary
   *"User is planning a recurring weekly NYC trip."*
4. **Current message** appended as the final `user` turn.
5. **Tools**: `search_flights`, `book_flight` (MCP), `remember_fact` (native).

The model now has the preference (aisle), the prior booking (episodic), the trip
context (summary), and the tools — and can call `search_flights` → `book_flight`
across loop iterations.

---

## 7. Memory subsystems — read & write paths

### 7.1 Working memory (Redis)
- **Read** (hot, every turn): last N turns + rolling summary for `conversation_id`.
- **Write** (synchronous, microseconds): append the completed turn.
- **Rollover:** when buffer exceeds N turns, the oldest turns are handed to an
  **async** summarizer (`invoke` + `response_model`) that folds them into the
  rolling summary, then dropped from the buffer.
- TTL on idle conversations; on expiry the conversation lives on only in episodic
  memory + the durable transcript (SQLite, if persisted).

### 7.2 Episodic memory (Qdrant)
- **Read** (hot, every turn): §6.4 retrieval, filtered by `user_id`.
- **Write** (async, off the hot path): after `TurnComplete`, enqueue
  `embed(turn) → VectorStore.add(...)`. One point per turn (or per user+assistant
  pair) with payload `{user_id, conversation_id, turn_id, text, role, ts}`.
- **Offline consolidation** (`llm_kit` batch engine): periodic jobs that summarize,
  deduplicate, or decay old points — this is where the batch pipeline earns its
  keep in this repo.

### 7.3 Factual memory (SQLite)
- **Read** (hot, every turn): `ProfileStore.get(user_id)` → compact profile.
- **Write** (async): two mechanisms, both off the hot path —
  1. **Extraction:** post-turn `invoke` + `response_model` extracts durable facts
     and `upsert_facts(...)`.
  2. **Tool-driven:** a native `remember_fact` tool the model can call mid-turn
     (see §8) — the most reliable way to capture "remember that I prefer X."
- Stored as structured columns + a JSON field; schema migrates SQLite→Postgres via
  SQLAlchemy with no code change above the store.

---

## 8. Tools / MCP

- **MCP client**: connect to configured MCP servers, discover tools, invoke them.
  Multi-server; tools namespaced by server to avoid collisions.
- **Tool registry**: unifies MCP-discovered tools and **native** in-repo tools
  behind one interface; `llm_kit`'s formatter renders the combined set into the
  provider tool schema.
- **Native tools (recommended even though loop is "MCP-first"):**
  - `remember_fact(key, value)` → `ProfileStore.upsert_facts` (factual writes).
  - (optional) `recall(query)` → explicit episodic search, for when the model
    wants to dig beyond the auto-injected top-k.
  - Episodic *retrieval* otherwise stays **automatic** (injected during context
    assembly, §6.4) — it's needed every turn and shouldn't cost a loop iteration.
- **Execution**: per-tool timeout, structured error capture, result truncation for
  display (full content still fed back to the model as the observation).

---

## 9. Persistence & data model

### 9.1 Redis (session store)
```
key  session:{conversation_id}
val  { working_buffer: [Turn, …],     # last N turns
       rolling_summary: str,
       scratch: dict,                  # agent-loop ephemeral state
       updated_at }
TTL  configurable idle expiry
```

### 9.2 SQLite (profile store, via SQLAlchemy + aiosqlite)
```
users(user_id PK, created_at, …)
profiles(user_id FK, facts JSON, structured cols…, updated_at)
conversations(conversation_id PK, user_id FK, started_at, …)   # durable metadata
messages(... )  # optional durable transcript if you want history beyond Redis
```
WAL mode on; single writer, many readers — fine for dozens concurrent. Migration
to Postgres is a connection-string change.

### 9.3 Qdrant (vector store)
```
collection  episodic_memory
point       { id, vector, payload: { user_id, conversation_id, turn_id,
                                      text, role, ts } }
filter      always by user_id on search
```

---

## 10. Serving layer

- **FastAPI** ASGI app.
- **Transport**: websocket (preferred for bidirectional chat) and/or SSE; both
  consume the `AgentEvent` stream and forward `TextDelta`s as they arrive.
- **Session lifecycle**: resolve `user_id` (auth stub for prototype) and
  `conversation_id`; load/create session state; stream `run_turn(...)`.
- **Backpressure**: bounded per-connection send; one slow client must not stall
  the shared event loop.
- **Health/metrics** endpoints (ties into §13 observability).

---

## 11. Configuration

Reuse `llm_kit`'s dataclass + YAML loader pattern (`${VAR}` interpolation,
nested dataclasses, `StrEnum`s). New config sections:

```
agent:     max_iterations, per_tool_timeout_s, per_turn_budget_s, system_prompt
memory:
  working:   buffer_turns (N), summary_trigger, ttl_s
  episodic:  top_k, min_score, query_augment_turns, query_rewrite (bool)
  factual:   extraction_enabled (bool)
context:   max_input_tokens, output_reserve_tokens, safety_margin
stores:
  redis:     url
  sqlite:    url            # sqlite+aiosqlite:///… → swap to postgresql+asyncpg:…
  qdrant:    url, collection
mcp:         servers: [ { name, transport, command/url, … } ]
llm:         (passed through to llm_kit — model, message_format, costs, rate limits)
```

---

## 12. Concurrency & scaling

- **Now:** single async process, dozens of concurrent conversations. State lives in
  the three external stores, not in the process, so workers are already
  near-stateless behind the Protocols.
- **Later (horizontal):** because Redis/SQLite(→Postgres)/Qdrant are shared and the
  app holds no un-shareable in-process state, scaling out is adding workers +
  swapping SQLite→Postgres. The design must keep it that way: **no module above
  `stores/` may cache mutable per-user state in process memory** without a
  shared-store backing.

---

## 13. Cross-cutting

- **Observability**: spans/metrics around turn latency, time-to-first-token, loop
  iterations, tool latency, retrieval hit rates, token usage per source (so the
  context budget is legible). Reuse `llm_kit`'s `UsageLedger` / `TokenUsage`.
- **Cost accounting**: `TokenUsage` per turn aggregated per user/conversation,
  priced via `llm_kit`'s per-1M-token config fields.

---

## 14. Build order (milestones)

1. **Skeleton + config + store Protocols** — empty adapters, contracts + tests.
2. **`stores/` adapters** — Redis / SQLite / Qdrant against the Protocols.
3. **`agent/` loop, no tools** — `invoke_stream` → `AgentEvent` stream, working
   memory only. Streaming end-to-end to a test client.
4. **Context construction + budgeter (§6)** — factual + episodic + summary
   assembly; the worked example (§6.6) as a test.
5. **`tools/` MCP client** — registry, execution, loop integration, safety rails.
6. **`memory/` write paths** — async episodic upsert, factual extraction + the
   `remember_fact` native tool, rolling-summary rollover.
7. **`serving/`** — FastAPI websocket/SSE, session lifecycle.
8. **Offline jobs** — `llm_kit` batch consolidation/re-embedding.
9. **Observability + cost accounting.**

---

## 15. Testing strategy

- **Stores**: contract tests against each Protocol; ephemeral Redis/Qdrant in
  Docker, in-memory/temp SQLite.
- **Agent loop**: a fake `invoke_stream` that emits scripted text + tool-call
  deltas (mirroring `llm_kit`'s `FakeLLM`), to assert event sequences, the
  `max_iterations` cap, and tool-error-as-observation.
- **Context builder**: golden tests — given fixed profile/episodic/buffer, assert
  the exact assembled message list and that the budgeter evicts in tier order.
- **MCP**: mock MCP server; assert discovery, namespacing, timeout handling.
- **No live-key integration tests in-repo** (same posture as `llm_kit`).

---

## 16. Open questions

- **Conversation history durability**: keep full transcripts in SQLite, or treat
  Redis (+ episodic) as the only retention? Affects the `messages` table in §9.2.
- **Multi-tenant isolation** beyond `user_id` filtering (separate Qdrant
  collections per tenant?) — defer until needed.
- **Query rewrite** (§6.4) default — latency vs. recall trade-off per deployment.
- **Episodic granularity** — one point per turn vs. per session-summary; affects
  retrieval precision and Qdrant growth.
