# Configuration Reference

This document describes all configuration options available in `config.yaml`. The config is global to all users; per-user state (memory, tool permissions) lives in the stores, never here.

## Top-Level Structure

```yaml
agent:        # Agent-loop safety rails and identity
memory:       # Working, episodic, and factual memory
context:      # Input-token budgeting
stores:       # Backend selection (in-memory, Redis, SQLite, Qdrant)
mcp:          # Model Context Protocol servers
tools:        # Tool allowlist and execution policies
skills:       # Skill discovery (agentskills.io format)
jobs:         # Offline batch job configuration
telemetry:    # Tracing via Langfuse/OpenTelemetry
metrics:      # Prometheus metrics
llm_kit:      # LLM and embedder configuration (nested)
```

---

## `agent` — Agent Loop and Identity

Controls the agent's behavior, safety rails, and system persona.

```yaml
agent:
  max_iterations: 6
  per_tool_timeout_s: 30.0
  per_turn_budget_s: null
  system_prompt: "You are a helpful assistant..."
  factual_block_header: "What you know about this user:"
  episodic_block_header: "Relevant memories from past conversations:"
  summary_block_header: "Summary of earlier in this conversation:"
  skills_block_header: "Available skills (use read_skill to load instructions):"
```

### Fields

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `max_iterations` | int | 6 | Maximum tool-calling loops per turn. Prevents runaway agent loops. |
| `per_tool_timeout_s` | float | 30.0 | Global fallback timeout (seconds) for any tool execution. Can be overridden per-tool in `tools.definitions`. |
| `per_turn_budget_s` | float \| null | null | Optional wall-clock timeout per turn (seconds). When set, the agent stops gracefully if the turn exceeds this budget. Null = no limit. |
| `system_prompt` | string | "You are a helpful assistant." | Base system message injected into every turn. Customize to set your assistant's persona. |
| `factual_block_header` | string | "What you know about this user:" | Header prefixed to the factual memory block in the system message. |
| `episodic_block_header` | string | "Relevant memories from past conversations:" | Header for episodic memory hits (past conversation summaries). |
| `summary_block_header` | string | "Summary of earlier in this conversation:" | Header for the rolling summary of earlier turns in the current conversation. |
| `skills_block_header` | string | "Available skills (use read_skill to load instructions):" | Header for the list of available skills. |

---

## `memory` — Memory Subsystems

Configures the three memory layers: working (in-conversation), episodic (past conversations), and factual (durable user profile).

### `memory.working` — In-Conversation Buffer

Manages the verbatim conversation buffer and triggers rolling-summary rollover.

```yaml
memory:
  working:
    buffer_turns: 12
    buffer_token_budget: 2048
    idle_finalize_s: 900
    ttl_s: 3600
    sweep_interval_s: 60
```

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `buffer_turns` | int | 12 | Maximum number of turns to keep verbatim in the buffer. When exceeded, oldest turns roll into the summary. |
| `buffer_token_budget` | int | 2048 | Target token size (estimated) for the verbatim buffer. When exceeded, oldest turns are summarized and dropped. This is **token-driven**, not turn-count-driven, so it adapts to turn size. |
| `idle_finalize_s` | int | 900 | Seconds of idle time before a conversation is finalized (embedded as an episodic point) but the session remains loadable. Must be < `ttl_s`. |
| `ttl_s` | int | 3600 | Seconds of idle time before a conversation's session is evicted from the store entirely. Should be ≥ `idle_finalize_s`. |
| `sweep_interval_s` | int | 60 | How often (seconds) the background idle sweeper scans for conversations due to finalize. Lower = faster finalization, higher = less frequent sweeps. |

**Rollover behavior:** When the buffer exceeds `buffer_token_budget`, the oldest turns are folded into a rolling summary (LLM-generated) and dropped. The summary is prepended to the system message on future turns. This keeps the verbatim buffer bounded while preserving durable facts and open threads.

**Two-stage idle lifecycle:** After `idle_finalize_s` seconds of idle, the conversation is embedded as one episodic point (so it's recallable in future conversations) but the session is kept in the store so the user can resume seamlessly. After `ttl_s` seconds, the session is evicted entirely (but the episodic embedding remains, archived indefinitely).

### `memory.episodic` — Past Conversation Retrieval

Configures vector-store retrieval of past conversation summaries.

```yaml
memory:
  episodic:
    enabled: true
    top_k: 3
    min_score: 0.3
    query_augment_turns: 2
    query_rewrite: false
    decay_rate: 0.05
    flagged_moments_enabled: false
    max_flagged_moments: 2
```

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `enabled` | bool | true | Master switch: if false, episodic memory is completely disabled—no embeddings, no vector-store calls. The `recall` tool becomes a no-op and the episodic block is never injected into the system message. |
| `top_k` | int | 3 | Number of top-scoring past conversation hits to retrieve and inject into the system message each turn. |
| `min_score` | float | 0.3 | Minimum similarity score (0–1) for a hit to be included. Hits below this are filtered out. Raise to be more selective; lower to be more permissive. |
| `query_augment_turns` | int | 2 | Number of recent turns to append to the user's message when building the retrieval query. Adds context so the vector search is more precise. |
| `query_rewrite` | bool | false | If true, the user's query is rewritten by the LLM into a standalone search query (resolving pronouns, etc.) before embedding and retrieval. Improves precision; adds LLM cost. |
| `decay_rate` | float | 0.05 | Temporal decay: multiplies retrieval scores by `exp(-rate × age_days)` at query time. 0.05 halves a score after ~14 days, biasing toward recent. Set to 0.0 to disable time decay. |
| `flagged_moments_enabled` | bool | false | If true, the LLM identifies notable discussion threads within each conversation at finalization and embeds them as sibling vector points (kind="moment"). Improves recall precision for specific topics. |
| `max_flagged_moments` | int | 2 | Maximum number of discussion threads to flag per conversation (when `flagged_moments_enabled: true`). |

**When to disable episodic memory:** Set `enabled: false` if your embedding endpoint is unavailable, you want to save embedding costs, or you prefer a stateless design. The system gracefully degrades—conversations still work, but past-conversation recall is unavailable.

### `memory.factual` — Durable User Profile

Configures extraction of long-term user facts (preferences, identity, constraints).

```yaml
memory:
  factual:
    extraction_enabled: true
```

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `extraction_enabled` | bool | true | If true, after each turn the LLM extracts durable facts about the user (e.g., "prefers Slack over email", "allergic to shellfish") and stores them in the profile. If false, no extraction occurs. |

**What gets extracted:** Preferences, identity, stable constraints. What does **not** get extracted: discussion topics, conversation context, situations the user was in (those belong in episodic memory).

### `memory.store_retry` — Background Write Retry Policy

Configures retry behavior for background memory writes (episodic finalization, working-memory rollover, etc.).

```yaml
memory:
  store_retry:
    max_retries: 3
    backoff_base_seconds: 0.2
    backoff_max_seconds: 5.0
    jitter_seconds: 0.1
```

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `max_retries` | int | 3 | Number of retry attempts for a failed store write. |
| `backoff_base_seconds` | float | 0.2 | Initial backoff duration (seconds) between retries. Increases exponentially. |
| `backoff_max_seconds` | float | 5.0 | Maximum backoff duration (seconds). Retries never wait longer than this. |
| `jitter_seconds` | float | 0.1 | Random jitter (seconds) added to each backoff to avoid thundering herds. |

---

## `context` — Context Budgeting

Controls the input-token budget for assembling messages before sending to the LLM.

```yaml
context:
  max_input_tokens: 128000
  output_reserve_tokens: 4096
  safety_margin: 1024
```

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `max_input_tokens` | int | 128000 | Maximum tokens available for the full input (system message + all messages + tools). If the LLM model has an 8k context window, set this to ~7500 to leave headroom. Must be less than your model's context window. |
| `output_reserve_tokens` | int | 4096 | Tokens reserved for the model's output (completion). Reduces budget available for input. Should match your `llm_kit.llm.max_tokens`. |
| `safety_margin` | int | 1024 | Additional margin (tokens) to avoid edge-case overflows. Reduces usable input budget. |

**Calculation:** Usable input budget = `max_input_tokens - output_reserve_tokens - safety_margin`.

**Tier-based eviction:** The budgeter evicts context in tiers (by priority): tier 0 (system + tools) never drops → tier 1 (factual) → tier 2 (working buffer, oldest first) → tier 3 (rolling summary) → tier 4 (episodic hits, lowest-score first).

---

## `stores` — Store Backends

Selects which persistence backend (in-memory, Redis, SQLite, Qdrant) each store uses.

```yaml
stores:
  session_backend: memory
  profile_backend: memory
  vector_backend: memory
  permission_backend: memory
  redis:
    url: ${REDIS_URL:-redis://localhost:6379/0}
  sqlite:
    url: ${SQLITE_URL:-sqlite+aiosqlite:///agent_kit.db}
  qdrant:
    mode: ${QDRANT_MODE:-host}
    path: ${QDRANT_PATH:-qdrant_data}
    url: ${QDRANT_URL:-http://localhost:6333}
    collection: episodic_memory
    vector_size: 1536
```

### Store Types

| Store | Purpose | Backend Options |
|-------|---------|-----------------|
| `session_backend` | Conversation sessions (buffer + summary) | memory, sqlite, redis |
| `profile_backend` | User profiles (factual facts) | memory, sqlite, redis |
| `vector_backend` | Episodic memory vector index | memory, qdrant |
| `permission_backend` | Per-user tool allowlists | memory, sqlite, redis |

### Backend Choices

- **`memory`** (default): In-process store, no external infra. Good for dev/testing. Lost on restart.
- **`redis`**: Shared, persistent key-value store. Good for multi-worker deployments.
- **`sqlite`**: File-based SQL database. Good for single-machine deployments; supports multiple connections.
- **`qdrant`**: Vector database for episodic memory. Required if using episodic retrieval. Supports three modes:

| Qdrant Mode | Use Case |
|-------------|----------|
| `memory` | In-memory (dev/testing). Lost on restart. |
| `file` | Disk-based (`path:` directory). Persistent single-process. |
| `host` | Remote Qdrant server (`url:`). Multi-worker safe. |

---

## `mcp` — Model Context Protocol Servers

Integrates external MCP servers to expand the tool suite dynamically.

```yaml
mcp:
  startup_timeout_s: 30.0
  servers:
    - name: filesystem
      transport: stdio
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "/data"]
      auto_allow: true
```

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `startup_timeout_s` | float | 30.0 | Per-server timeout (seconds) for connection + tool discovery. If exceeded, the server is skipped and logged. |
| `servers[].name` | string | (required) | Unique server name. Tool names from this server are namespaced `{name}__{tool_name}`. |
| `servers[].transport` | enum | stdio | Transport type: `stdio` (subprocess), `http` (streamable HTTP), `sse` (server-sent events). |
| `servers[].command` | string | null | For `stdio`: executable command (e.g., `npx`, `python`). |
| `servers[].args` | list[string] | [] | Command arguments. |
| `servers[].url` | string | null | For `http` or `sse`: remote server URL. |
| `servers[].auto_allow` | bool | false | If true, discovered tools are folded into the default allowlist at startup. Use only for trusted servers. |

**Discovery:** MCP servers are connected and their tools discovered at startup (in `AgentService.astart()`). A server that fails within `startup_timeout_s` is logged and skipped; others continue.

---

## `tools` — Tool Configuration

Controls which tools are available by default and per-tool execution policies.

```yaml
tools:
  default_allowed:
    - remember_fact
    - forget_fact
    - list_facts
    - recall
  definitions:
    forget_memory:
      requires_approval: true
      approval_timeout_s: 30
    slow_external_api:
      timeout_s: 60.0
    high_volume_tool:
      rate_limit_per_minute: 30
```

### `default_allowed`

List of tool names that all users can execute by default. Examples: `remember_fact`, `list_facts`, `recall`, `forget_memory`. If a user has no per-user grant in the store, they inherit this list.

**Built-in memory tools** (always available if memory is enabled):
- `remember_fact` — Extract and store a fact in the user's profile.
- `forget_fact` — Remove a fact from the profile.
- `list_facts` — Retrieve all facts for the user.
- `recall` — Search episodic memory (past conversations).
- `forget_memory` — Delete an episodic embedding irreversibly.
- `read_skill` — Read full skill instructions (if skills are discovered).

### `definitions` — Per-Tool Policies

Override global defaults for specific tools.

```yaml
tools:
  definitions:
    tool_name:
      timeout_s: 60.0            # override global per_tool_timeout_s
      rate_limit_per_minute: 30  # enforce per-user rate limit
      requires_approval: true     # pause agent, wait for human approval
      approval_timeout_s: 30      # timeout if no approval within this time
```

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `timeout_s` | float | null | Tool-specific timeout (seconds). Null = use global `agent.per_tool_timeout_s`. |
| `rate_limit_per_minute` | int | null | Rate limit (calls per minute per user). Null = unlimited. In-process, so multi-worker deploys enforce ~workers × this limit. |
| `requires_approval` | bool | false | If true, pause the agent loop before executing and emit a `ToolApprovalRequired` event. Over WebSocket, the client responds with `{"type":"approval","call_id":…,"approved":bool}`; over SSE, auto-deny. |
| `approval_timeout_s` | float | 30.0 | If approval is requested, auto-deny after this many seconds. |

**Example:** `forget_memory` usually has `requires_approval: true` in config because it irreversibly deletes embeddings.

---

## `skills` — Skill Discovery

Integrates agentskills.io-format skill files (directories with `SKILL.md`).

```yaml
skills:
  paths:
    - ./skills
    - ${EXTRA_SKILLS_PATH:-./extra_skills}
```

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `paths` | list[string] | [] | Directories to scan for `SKILL.md` files at startup. Paths support `${VAR}` variable interpolation. |

**How it works:**
1. At startup, each directory is scanned for immediate subdirectories containing a `SKILL.md` file.
2. Each `SKILL.md` is parsed (YAML frontmatter + Markdown body) and listed in the system message.
3. The agent can call `read_skill(name)` to retrieve the full skill body and follow its instructions.

---

## `jobs` — Offline Batch Jobs

Configures the episodic memory maintenance batch jobs (deduplication, re-summarization).

```yaml
jobs:
  deduplication:
    similarity_threshold: 0.92
    max_points_per_user: 10000
    worker_concurrency: 8
  resummarization:
    min_age_days: 90.0
    max_points_per_user: 500
    worker_concurrency: 8
```

### `deduplication` — Merge Near-Identical Conversations

Clusters near-identical episodic points (via cosine similarity + Union-Find) and merges them via LLM.

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `similarity_threshold` | float | 0.92 | Cosine similarity threshold (0–1) for clustering. 0.92 is tight (reconnected-session duplicates / near-verbatim paraphrases). Lower carefully; 0.85 may merge topic-adjacent but distinct conversations. |
| `max_points_per_user` | int | 10000 | Per-user cap on points processed. Limits job scope. |
| `worker_concurrency` | int | 8 | Number of concurrent LLM calls during merging. |

**Run:** `python -m agent_kit.jobs dedup --config config.yaml --users alice,bob`

### `resummarization` — Refresh Old Summaries

Refreshes and re-embeds episodic points older than `min_age_days`.

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `min_age_days` | float | 90.0 | Minimum age (days) for a point to be re-summarized. Older points are condensed and re-embedded to keep retrieval quality high. |
| `max_points_per_user` | int | 500 | Per-user cap on points processed. |
| `worker_concurrency` | int | 8 | Number of concurrent LLM calls during re-summarization. |

**Run:** `python -m agent_kit.jobs resummarize --config config.yaml --users alice,bob`

---

## `telemetry` — Tracing and Observability

Integrates Langfuse (built on OpenTelemetry) for tracing and observability.

```yaml
telemetry:
  enabled: ${LANGFUSE_ENABLED:-false}
  service_name: agent_kit
  sample_rate: 1.0
  environment: ${LANGFUSE_ENVIRONMENT:-}
  release: ${LANGFUSE_RELEASE:-}
```

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `enabled` | bool | false | Master switch. If false, no traces are recorded (no-op). |
| `service_name` | string | "agent_kit" | Service identifier in Langfuse. |
| `sample_rate` | float | 1.0 | Head sampling ratio (0–1). 1.0 = trace every turn; 0.1 = trace ~10%. |
| `environment` | string | "" | Environment tag (e.g., "production", "staging") surfaced in Langfuse. |
| `release` | string | "" | Release tag (e.g., "v1.2.3") surfaced in Langfuse. |

**Credentials:** Read from environment at runtime: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`. Never put API keys in this file.

**Trace structure:**
- Root span: per-turn (`conversation_id` → Langfuse session, `user_id` → Langfuse user)
  - Context assembly span
  - LLM generation span (with token usage → Langfuse prices it)
  - Per-tool execution span (with outcome: ok / denied / timeout / rate-limited)
- Separate root: conversation finalization

**Setup:** Install the `telemetry` extra (`uv sync --extra telemetry`) and set credentials in environment.

---

## `metrics` — Prometheus Metrics

Exports Prometheus metrics at `GET /metrics`.

```yaml
metrics:
  enabled: false
```

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `enabled` | bool | false | Master switch. If false, `GET /metrics` returns 501; if true, returns Prometheus text format. |

**Instruments exported:**
- `agent_kit_ttft_seconds` — Time to first token (histogram).
- `agent_kit_turn_latency_seconds` — Total turn latency (histogram).
- `agent_kit_turn_iterations` — Tool iterations per turn (histogram).
- `agent_kit_tool_calls_total` — Tool calls with outcome labels (counter). Labels: `tool`, `outcome` (ok / not_permitted / rate_limited / timeout / error).
- `agent_kit_retrieval_hits` — Episodic retrieval hit count (histogram).

---

## `llm_kit` — LLM and Embedder Configuration

Nested block handed to `llm_kit`'s `AppConfig`. Configures the LLM provider, embedding model, and rate limiting.

```yaml
llm_kit:
  llm:
    message_format: anthropic
    base_url: ${LLM_BASE_URL:-https://api.anthropic.com}
    chat_completions_path: /v1/messages
    model: ${LLM_MODEL:-claude-haiku-4-5-20251001}
    api_key_env: ANTHROPIC_API_KEY
    max_tokens: 4096
  embed:
    base_url: ${EMBED_BASE_URL:-http://127.0.0.1:1234}
    model: ${EMBED_MODEL:-text-embedding-qwen3-embedding-0.6b}
    api_key_env: OPENAI_API_KEY
  rate_limit:
    max_concurrent_requests: 16
    requests_per_minute: 500
```

### `llm_kit.llm` — Language Model

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `message_format` | string | "anthropic" | Wire format for messages. "anthropic" for Claude; "openai" for OpenAI-compatible. |
| `base_url` | string | "https://api.anthropic.com" | Base URL for the LLM provider. Supports `${VAR}` interpolation. |
| `chat_completions_path` | string | "/v1/messages" | API endpoint path for chat completions. |
| `model` | string | "claude-haiku-4-5-20251001" | Model ID/name. Supports `${VAR}`. |
| `api_key_env` | string | "ANTHROPIC_API_KEY" | Environment variable name holding the API key. |
| `max_tokens` | int | 4096 | Maximum tokens per completion. Matches the output reserve in `context.output_reserve_tokens`. |

**Supported models:** Any OpenAI-compatible or Anthropic provider. Examples:
- Claude: `claude-opus-4-8`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`
- OpenAI: `gpt-4-turbo`, `gpt-4o`
- Local: OpenAI-compatible servers (LM Studio, vLLM, etc.)

### `llm_kit.embed` — Embedding Model

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `base_url` | string | "http://127.0.0.1:1234" | Base URL for the embedder. Usually a local or remote OpenAI-compatible server. |
| `model` | string | "text-embedding-qwen3-embedding-0.6b" | Embedding model ID. |
| `api_key_env` | string | "OPENAI_API_KEY" | Environment variable name holding the API key (if needed). |

**Note:** Embedding is **only used** if episodic memory is enabled (`memory.episodic.enabled: true`). If you disable episodic memory, you don't need a working embedder.

**Popular choices:**
- Ollama: `EMBED_BASE_URL=http://localhost:11434/v1` + `EMBED_MODEL=nomic-embed-text`
- OpenAI: `EMBED_BASE_URL=https://api.openai.com/v1` + `EMBED_MODEL=text-embedding-3-small`
- Hugging Face: Locally via LM Studio or vLLM

**Vector size:** Must match your Qdrant config (`stores.qdrant.vector_size`). Most models output 1536 dimensions (e.g., text-embedding-3-small, text-embedding-qwen3-0.6b).

### `llm_kit.rate_limit` — Concurrency and Rate Limiting

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `max_concurrent_requests` | int | 16 | Maximum concurrent HTTP requests to the LLM/embedder. |
| `requests_per_minute` | int | 500 | Rate limit (requests per minute) enforced globally. |

---

## Variable Interpolation

All string values support `${VAR}` and `${VAR:-default}` interpolation from environment variables.

**Examples:**
```yaml
llm_kit:
  llm:
    api_key_env: ANTHROPIC_API_KEY      # read from env var
    model: ${LLM_MODEL:-claude-haiku-4-5-20251001}  # or default
stores:
  sqlite:
    url: ${SQLITE_URL:-sqlite+aiosqlite:///agent_kit.db}
```

---

## Quick Start Examples

### Minimal (Dev/Testing)
```yaml
agent:
  system_prompt: "You are a helpful assistant."

memory:
  episodic:
    enabled: false  # skip embedding

stores:
  session_backend: memory
  profile_backend: memory
  vector_backend: memory
  permission_backend: memory

llm_kit:
  llm:
    model: claude-haiku-4-5-20251001
    api_key_env: ANTHROPIC_API_KEY
```

### Single Machine (SQLite, No Episodic)
```yaml
agent:
  system_prompt: "You are a helpful assistant with memory."

memory:
  episodic:
    enabled: false

stores:
  session_backend: sqlite
  profile_backend: sqlite
  vector_backend: memory  # not used
  permission_backend: sqlite

llm_kit:
  llm:
    model: claude-sonnet-4-6
    api_key_env: ANTHROPIC_API_KEY
```

### Production (Multi-Worker, Full Featured)
```yaml
agent:
  max_iterations: 10
  per_tool_timeout_s: 60
  system_prompt: "You are a helpful assistant..."

memory:
  episodic:
    enabled: true
    top_k: 5
    flagged_moments_enabled: true

stores:
  session_backend: redis
  profile_backend: redis
  vector_backend: qdrant
  permission_backend: redis
  qdrant:
    mode: host
    url: ${QDRANT_URL:-http://qdrant:6333}

telemetry:
  enabled: true
  sample_rate: 0.5

llm_kit:
  llm:
    model: ${LLM_MODEL:-claude-opus-4-8}
    api_key_env: ANTHROPIC_API_KEY
    max_tokens: 4096
  embed:
    base_url: ${EMBED_BASE_URL}
    model: ${EMBED_MODEL}
  rate_limit:
    requests_per_minute: 1000
```

---

## Validation

The config is validated at load time. Common errors:

- **`idle_finalize_s >= ttl_s`**: Conversations would be evicted before finalization. Fix: ensure `idle_finalize_s < ttl_s`.
- **Duplicate MCP server names**: Tool namespacing depends on uniqueness. Fix: rename servers.
- **`vector_size` mismatch**: Qdrant vector size must match the embedder output. Check your embedder's dimension.

---

## Environment Variables

If you don't want to store credentials in `config.yaml`, use environment variable interpolation:

```bash
export ANTHROPIC_API_KEY="sk-..."
export OPENAI_API_KEY="sk-..."
export LANGFUSE_PUBLIC_KEY="..."
export LANGFUSE_SECRET_KEY="..."
export QDRANT_URL="http://qdrant:6333"
export LLM_MODEL="claude-opus-4-8"
```

Then reference them in config:

```yaml
llm_kit:
  llm:
    api_key_env: ANTHROPIC_API_KEY
    model: ${LLM_MODEL}
  embed:
    api_key_env: OPENAI_API_KEY
    base_url: ${EMBED_BASE_URL}
```
