"""Config dataclass tree for agent_kit.

The single ``config.yaml`` carries agent_kit's own sections **plus** a nested
``llm_kit`` block that maps onto ``llm_kit``'s ``AppConfig``. Config is global
to all users; only memory and tool permissions are per-user (resolved at
runtime from the stores), never from this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from llm_kit import AppConfig


class StoreBackend(StrEnum):
    """Which adapter implementation a store should use."""

    MEMORY = "memory"
    REDIS = "redis"
    SQLITE = "sqlite"
    QDRANT = "qdrant"


class McpTransport(StrEnum):
    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


@dataclass(slots=True)
class AgentConfig:
    """Agent-loop safety rails and identity (SPEC §11)."""

    max_iterations: int = 6
    per_tool_timeout_s: float = 30.0
    per_turn_budget_s: float | None = None
    system_prompt: str = "You are a helpful assistant."


@dataclass(slots=True)
class WorkingMemoryConfig:
    buffer_turns: int = 12
    # Rollover is token-budget-driven (not a fixed turn count): when the buffer's
    # estimated size exceeds this, the oldest turns are summarized into the rolling
    # summary and dropped. Keeps the verbatim buffer bounded regardless of turn size.
    buffer_token_budget: int = 2048
    # Two-stage idle lifecycle (must satisfy idle_finalize_s < ttl_s):
    #   idle_finalize_s — after this much idle, the conversation is *finalized*
    #     (embedded as one episodic point) but the session is kept loadable so the
    #     user can resume seamlessly. This is the transport-agnostic backstop that
    #     gives SSE the conversation-end signal a WebSocket disconnect provides.
    #   ttl_s — after this much idle, the session is *evicted* from the store. By
    #     then it has already been finalized, so no memory is lost on eviction.
    idle_finalize_s: int = 900
    ttl_s: int = 3600
    # How often the idle sweeper scans for conversations due to finalize.
    sweep_interval_s: int = 60

    def __post_init__(self) -> None:
        if self.idle_finalize_s >= self.ttl_s:
            raise ValueError(
                "working memory: idle_finalize_s must be < ttl_s so a conversation "
                f"is finalized before it is evicted (got idle_finalize_s="
                f"{self.idle_finalize_s}, ttl_s={self.ttl_s})"
            )


@dataclass(slots=True)
class EpisodicMemoryConfig:
    top_k: int = 3
    min_score: float = 0.3
    query_augment_turns: int = 2
    query_rewrite: bool = False


@dataclass(slots=True)
class FactualMemoryConfig:
    extraction_enabled: bool = True


@dataclass(slots=True)
class StoreRetryConfig:
    """Retry policy for background store writes (maps onto ``retry.RetryPolicy``).

    Covers agent_kit's own plain store writes only — the LLM ``invoke`` and embedder
    calls in the same background ops are already retried by llm_kit's ``http.llm_retry``.
    Defaults are smaller than llm_kit's HTTP retry (max_retries=5, base=0.5, max=30s)
    because store writes are lower-latency and a long backoff would delay off-hot-path
    rollover/finalize.
    """

    max_retries: int = 3
    backoff_base_seconds: float = 0.2
    backoff_max_seconds: float = 5.0
    jitter_seconds: float = 0.1


@dataclass(slots=True)
class MemoryConfig:
    working: WorkingMemoryConfig = field(default_factory=WorkingMemoryConfig)
    episodic: EpisodicMemoryConfig = field(default_factory=EpisodicMemoryConfig)
    factual: FactualMemoryConfig = field(default_factory=FactualMemoryConfig)
    store_retry: StoreRetryConfig = field(default_factory=StoreRetryConfig)


@dataclass(slots=True)
class ContextConfig:
    """Input-token budget for context assembly (SPEC §6.5)."""

    max_input_tokens: int = 128_000
    output_reserve_tokens: int = 4_096
    safety_margin: int = 1_024


@dataclass(slots=True)
class RedisConfig:
    url: str = "redis://localhost:6379/0"


@dataclass(slots=True)
class SqliteConfig:
    # sqlite+aiosqlite:///… → swap to postgresql+asyncpg:… with no code change.
    url: str = "sqlite+aiosqlite:///agent_kit.db"


@dataclass(slots=True)
class QdrantConfig:
    url: str = "http://localhost:6333"
    collection: str = "episodic_memory"


@dataclass(slots=True)
class StoresConfig:
    """Backend selection + per-store connection details.

    Defaults to the in-memory reference adapters so the service runs with zero
    external infrastructure; flip a ``*_backend`` to swap in a real adapter.
    """

    session_backend: StoreBackend = StoreBackend.MEMORY
    profile_backend: StoreBackend = StoreBackend.MEMORY
    vector_backend: StoreBackend = StoreBackend.MEMORY
    permission_backend: StoreBackend = StoreBackend.MEMORY
    redis: RedisConfig = field(default_factory=RedisConfig)
    sqlite: SqliteConfig = field(default_factory=SqliteConfig)
    qdrant: QdrantConfig = field(default_factory=QdrantConfig)


@dataclass(slots=True)
class McpServerConfig:
    name: str
    transport: McpTransport = McpTransport.STDIO
    command: str | None = None
    url: str | None = None
    args: list[str] = field(default_factory=list)


@dataclass(slots=True)
class McpConfig:
    servers: list[McpServerConfig] = field(default_factory=list)


@dataclass(slots=True)
class ToolsConfig:
    """Global fallback allowlist for users with no per-user grant in the store."""

    default_allowed: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AgentKitConfig:
    """Top-level config. Compose from YAML via ``AgentKitConfig.from_yaml``."""

    agent: AgentConfig = field(default_factory=AgentConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    stores: StoresConfig = field(default_factory=StoresConfig)
    mcp: McpConfig = field(default_factory=McpConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    llm_kit: AppConfig = field(default_factory=AppConfig)

    @classmethod
    def from_yaml(cls, path: str) -> AgentKitConfig:
        from agent_kit.config.loader import load_yaml

        return load_yaml(cls, path)

    @classmethod
    def from_dict(cls, data: dict) -> AgentKitConfig:
        from agent_kit.config.loader import load_dict

        return load_dict(cls, data)
