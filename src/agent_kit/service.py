"""Composition root: wire config → stores → memory → tools → agent.

``AgentService`` owns the shared ``httpx.AsyncClient`` (one session feeding both
the LLM client and the embedder), the store bundle, and the assembled ``Agent``.
``serving/`` constructs one of these and streams ``agent.run_turn`` per request.

Building the real llm_kit clients is isolated here so the rest of the package
depends only on the ``LLM`` / ``Embedder`` Protocols and is testable with fakes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Self

from llm_kit import LLMClient, OpenAICompatibleEmbedder
from llm_kit.http.session import build_async_client

from agent_kit.agent.budgeter import ContextBudgeter
from agent_kit.agent.context import ContextBuilder
from agent_kit.agent.loop import Agent
from agent_kit import telemetry, metrics as _metrics
from agent_kit.config import AgentKitConfig
from agent_kit.llm import LLM, Embedder, TracingEmbedder, TracingLLM
from agent_kit.memory.episodic import EpisodicMemory
from agent_kit.memory.factual import FactualMemory
from agent_kit.memory.working import WorkingMemory
from agent_kit.retry import RetryPolicy
from agent_kit.stores.factory import Stores, build_stores
from agent_kit.stores.types import SessionState
from agent_kit.tools.base import Tool
from agent_kit.tools.mcp import McpClient, MCPManager
from agent_kit.tools.native import (
    forget_fact_tool,
    forget_memory_tool,
    list_facts_tool,
    recall_tool,
    remember_fact_tool,
)
from agent_kit.tools.registry import ToolRegistry
from agent_kit.tools.skill_tools import read_skill_tool
from agent_kit.skills import SkillManager, discover


def _as_async(fn):
    """Adapt a sync cleanup (``telemetry.shutdown``) to the awaited cleanup list."""

    async def _run() -> None:
        fn()

    return _run


@dataclass(slots=True)
class AgentService:
    cfg: AgentKitConfig
    stores: Stores
    agent: Agent
    registry: ToolRegistry
    mcp: MCPManager
    skill_manager: SkillManager | None = None
    _shared_client: object = None   # httpx.AsyncClient; kept for the LLM factory
    _llm_factory: object = None     # Callable[[str], LLM] | None
    _aclose: object = None          # callable cleanup, set by build()

    @classmethod
    def from_yaml(cls, path: str) -> Self:
        return cls.build(AgentKitConfig.from_yaml(path))

    @classmethod
    def build(
        cls,
        cfg: AgentKitConfig,
        *,
        llm: LLM | None = None,
        embedder: Embedder | None = None,
        extra_tools: list[Tool] | None = None,
        mcp_clients: list[McpClient] | None = None,
        system_prompt_fn: Callable[[str, str], Awaitable[str]] | None = None,
    ) -> Self:
        """Assemble the service. Inject ``llm``/``embedder`` for tests; otherwise
        the real llm_kit clients are built over one shared HTTP session."""
        # Configure tracing + metrics first so every span/record below is captured. Both
        # are no-ops when disabled (the default), so tests are unaffected.
        telemetry.configure(cfg.telemetry)
        _metrics.configure(cfg.metrics)

        shared_client = None
        cleanups = []
        llm_built_internally = llm is None
        if llm is None or embedder is None:
            shared_client = build_async_client(cfg.llm_kit.http)
        if llm is None:
            real_llm = LLMClient(cfg.llm_kit, client=shared_client, owns_client=False)
            llm = real_llm
            cleanups.append(real_llm.aclose)
        if embedder is None:
            real_embedder = OpenAICompatibleEmbedder(
                cfg.llm_kit, client=shared_client, owns_client=False
            )
            embedder = real_embedder
            cleanups.append(real_embedder.aclose)
        if shared_client is not None:
            cleanups.append(shared_client.aclose)

        # Wrap LLM/embedder so every call (hot-path stream, background invoke, embed)
        # becomes a Langfuse generation under the active span — a single chokepoint that
        # also catches the memory layer's direct calls. Only when enabled, so the
        # FakeLLM suite runs the bare client. ``shutdown`` flushes spans on teardown.
        if telemetry.is_enabled():
            llm = TracingLLM(llm, model=cfg.llm_kit.llm.model)
            embedder = TracingEmbedder(embedder, model=cfg.llm_kit.embed.model)
            cleanups.append(_as_async(telemetry.shutdown))

        # Build a per-model LLM factory only when the service manages its own client.
        # The factory reuses the shared HTTP session and caches built clients by model
        # name so subsequent calls for the same model are free. None when an LLM was
        # externally injected (test path), which disables set_conversation_model().
        llm_factory = None
        if llm_built_internally:
            import dataclasses as _dc
            _llm_cache: dict[str, LLM] = {}

            def _make_llm(model_name: str) -> LLM:
                if model_name not in _llm_cache:
                    new_cfg = _dc.replace(
                        cfg.llm_kit,
                        llm=_dc.replace(cfg.llm_kit.llm, model=model_name),
                    )
                    client: LLM = LLMClient(new_cfg, client=shared_client, owns_client=False)
                    if telemetry.is_enabled():
                        client = TracingLLM(client, model=model_name)
                    _llm_cache[model_name] = client
                return _llm_cache[model_name]

            llm_factory = _make_llm

        # Discover skills (sync filesystem I/O — safe in build()).
        skill_manager: SkillManager | None = None
        # forget_memory is seeded only when episodic memory is enabled; it is meaningless
        # without a vector store to delete from. The ToolPolicy (requires_approval) adds
        # the HITL gate when it is active.
        extra_default_tools: set[str] = {"forget_memory"} if cfg.memory.episodic.enabled else set()
        if cfg.skills.paths:
            skill_manager = SkillManager(discover(cfg.skills.paths))
            if skill_manager.list_all():
                extra_default_tools.add("read_skill")

        stores = build_stores(cfg, extra_default_allowed=extra_default_tools)
        # Map the config's StoreRetryConfig onto the retry leaf's RetryPolicy (kept as
        # two types so retry.py need not import config). Shared across all three writes.
        store_retry = RetryPolicy(
            max_retries=cfg.memory.store_retry.max_retries,
            backoff_base_seconds=cfg.memory.store_retry.backoff_base_seconds,
            backoff_max_seconds=cfg.memory.store_retry.backoff_max_seconds,
            jitter_seconds=cfg.memory.store_retry.jitter_seconds,
        )
        working = WorkingMemory(
            stores.session, cfg.memory.working, llm=llm, store_retry=store_retry
        )
        episodic: EpisodicMemory | None = (
            EpisodicMemory(
                stores.vectors, embedder, cfg.memory.episodic, llm=llm, store_retry=store_retry
            )
            if cfg.memory.episodic.enabled
            else None
        )
        factual = FactualMemory(
            stores.profile, cfg.memory.factual, llm=llm, store_retry=store_retry
        )

        tools: list[Tool] = [
            remember_fact_tool(factual, episodic_enabled=cfg.memory.episodic.enabled),
            forget_fact_tool(factual),
            list_facts_tool(factual),
        ]
        if episodic is not None:
            tools.append(recall_tool(episodic))
            tools.append(forget_memory_tool(episodic))
        if skill_manager and skill_manager.list_all():
            tools.append(read_skill_tool(skill_manager, stores.skills))
        if extra_tools:
            tools.extend(extra_tools)
        registry = ToolRegistry(
            tools,
            stores.permissions,
            per_tool_timeout_s=cfg.agent.per_tool_timeout_s,
            policies=cfg.tools.definitions,
        )
        # MCP servers are connected lazily in ``astart`` (an async step build() can't
        # await); discovered tools are registered then.
        mcp = MCPManager(
            cfg.mcp.servers,
            startup_timeout_s=cfg.mcp.startup_timeout_s,
            clients=mcp_clients,
        )

        builder = ContextBuilder(
            agent_cfg=cfg.agent,
            working=working,
            episodic=episodic,
            factual=factual,
            registry=registry,
            budgeter=ContextBudgeter(cfg.context),
            system_prompt_fn=system_prompt_fn,
            skill_manager=skill_manager,
            skill_store=stores.skills,
        )
        agent = Agent(llm, builder, registry, working, episodic, factual, cfg.agent,
                      llm_factory=llm_factory)

        async def _aclose() -> None:
            await agent.drain()
            await mcp.aclose()
            for close in cleanups:
                await close()

        return cls(
            cfg=cfg,
            stores=stores,
            agent=agent,
            registry=registry,
            mcp=mcp,
            skill_manager=skill_manager,
            _shared_client=shared_client,
            _llm_factory=llm_factory,
            _aclose=_aclose,
        )

    async def set_conversation_model(
        self, conversation_id: str, user_id: str, model_name: str | None
    ) -> None:
        """Set (or clear with ``None``) the model override for a conversation.

        The override is stored in the session and picked up by the next
        ``run_turn`` call for that conversation. Other conversations are unaffected.

        Raises ``ValueError`` when the service was built with an externally-injected
        LLM (test path — no factory available to construct per-model clients).
        Raises ``UnauthorizedError`` if ``user_id`` does not own the conversation.
        """
        if self._llm_factory is None:
            raise ValueError(
                "set_conversation_model requires the service to manage its own LLM "
                "client (not available when LLM was externally injected)"
            )
        state = await self.stores.session.load(conversation_id, user_id)
        if state is None:
            state = SessionState(user_id=user_id)
        state.model_name = model_name
        await self.stores.session.save(conversation_id, state)

    async def astart(self) -> None:
        """Connect configured MCP servers and register their discovered tools.

        Idempotent enough for the common path (no servers → no-op). Call once after
        ``build`` and before serving turns; the serving lifespan does this.
        """
        tools, auto_allowed = await self.mcp.start()
        for tool in tools:
            self.registry.register(tool)
        if auto_allowed:
            await self.stores.permissions.extend_default_allowed(auto_allowed)

    async def aclose(self) -> None:
        if callable(self._aclose):
            await self._aclose()
