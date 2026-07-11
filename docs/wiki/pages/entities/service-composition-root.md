---
title: Service Composition Root
category: entity
tags: [composition, wiring, config, dependency-injection, factories]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/service.py, CLAUDE.md#Per-conversation model switching]
status: current
---

# Service Composition Root

`AgentService` is the composition root: it wires [[pages/config/schema|config]] → [[pages/entities/stores-overview|stores]] → [[pages/memory|memory]] → [[pages/entities/tool-registry|tools]] → [[pages/entities/agent-loop|agent]].

## Responsibilities

**One shared HTTP client.** `httpx.AsyncClient` is built once and handed to both the LLM client and the embedder, ensuring they share the same connection pool, SSL session, and configuration. Closed at app shutdown.

**Store bundle.** `Stores` (dataclass with five store instances) is built from config via `build_stores()`. Factories select backend adapters (in-memory for testing, real backends for production).

**Memory system.** [[pages/entities/working-memory]], [[pages/entities/episodic-memory]], and [[pages/entities/factual-memory]] are instantiated with the stores and rate-limit policies.

**Tool registry.** Native tools (remember_fact, recall, etc.) and skill_tools (read_skill) are wired; MCP tools are discovered later in `astart()`.

**Agent assembly.** [[pages/entities/agent-loop|Agent]] is constructed with memory, stores, tools, context builder, budgeter, and policies.

## LLM Factory

If the service builds its own `LLMClient` (not externally injected with a `FakeLLM`), a `_make_llm` closure is created to support [[pages/decisions/per-conversation-model-switching|per-conversation model switching]]:

- Captures the shared `httpx.AsyncClient`, `cfg.llm_kit`, and a `_llm_cache` dict
- On first use of a model name, builds an `LLMClient` (same pool/keys, only `model` differs)
- Caches the client by model name for reuse (subsequent calls are free)

The factory is `None` when `llm` is injected (test path) — `set_conversation_model` raises `ValueError` in that case.

## Lifetimes

**Sync build:**
1. Config is loaded and validated
2. Stores are initialized (in-memory or connection pools opened)
3. Native tools are wired
4. Agent is constructed
5. `AgentService` is returned (ready to serve)

**Async startup (`astart()`):**
1. MCP servers are connected and their tools discovered
2. MCP tools are registered
3. Ready to accept requests

**Async shutdown (`aclose()`):**
1. Awaits pending background writes
2. Closes MCP connections
3. Closes the shared HTTP client

## Layering

`service.py` is at the top of the stack (see [[pages/concepts/bottom-up-layering]]). It's the only place that:

- Imports `llm_kit` directly (concrete `LLMClient` and `Embedder` clients)
- Constructs `Stores` (pairs Protocols with adapters)
- Wires everything together

All layers below (agent, tools, memory) depend only on the [[pages/entities/llm-protocols|LLM/Embedder Protocols]], not concrete clients.

## Testing

In tests, `make_service(cfg, llm=FakeLLM(...), ...)` builds a complete service with a fake LLM. The wiring is identical; only the LLM is replaced. This enables full integration tests without live API calls.
