---
title: Harness Kit Overview
category: entity
tags: [architecture, service, agentic, multi-user]
created: 2026-07-11
updated: 2026-07-11
sources: [CLAUDE.md#What this is, src/harness_kit/service.py, ROADMAP.md]
status: current
---

# Harness Kit Overview

A stateful, multi-turn **agentic chatbot service** built on top of [`llm_kit`](https://github.com/sharma-n/llm_kit).

## Core Identity

**What llm_kit provides:** provider wire formats, streaming `invoke_stream`, structured `invoke`, embedder, rate limiting, retries, and error hierarchy.

**What harness_kit adds:** conversation state, memory (three-part: [[pages/entities/working-memory]], [[pages/entities/episodic-memory]], [[pages/entities/factual-memory]]), a tool-calling [[pages/entities/agent-loop]], and a serving layer.

**Optimization focus:** the opposite of llm_kit. Prioritizes:
- Long-lived sessions (not batch throughput)
- Time-to-first-token latency (not maximum utilization)
- Per-user state isolation ([[pages/concepts/multi-user-scoping]])
- Async end-to-end ([[pages/concepts/async-end-to-end]])

## Architectural Layers

See [[pages/concepts/bottom-up-layering]] for the non-negotiable strict import order:

```
config → stores → (skills || tools) → agent → serving
```

## Core Abstractions

The [[pages/concepts/agent-event-stream]] is the primary interface for streaming. Tool execution follows [[pages/concepts/tool-errors-as-observations]]. The [[pages/entities/context-budgeter]] enforces resource limits via tiered eviction.

## Integration Points

- **LLM abstraction:** [[pages/entities/llm-protocols]]
- **Tool execution:** [[pages/entities/tool-registry]]
- **Skills extension:** [[pages/entities/skills-system]]
- **Serving transports:** [[pages/entities/serving-layer]]
