---
title: Bottom-Up Layering
category: concept
tags: [architecture, layering, dependencies]
created: 2026-07-11
updated: 2026-07-11
sources: [CLAUDE.md#The non-negotiable rule: strict bottom-up layering]
status: current
---

# Bottom-Up Layering

A non-negotiable architectural rule: **each layer imports only from layers below it**.

## The Stack

```
config
  ↓
stores
  ↓
skills || tools  (same level — neither imports the other)
  ↓
agent
  ↓
serving
```

## Key Principles

**No upward imports.** If you want a lower layer to use a higher-layer abstraction (e.g., `tools/` importing `agent/events.py`), don't — pass primitives up instead. The [[pages/entities/tool-registry]] returns plain `Execution` primitives; the [[pages/entities/agent-loop]] maps them to `AgentEvent` types.

**Composition root:** `service.py` is the only place that wires together all layers. It controls the entire assembly from config down.

**Protocol boundaries:** [[pages/entities/llm-protocols]] sit at the `config/stores` boundary so every layer above depends on the abstraction, not the concrete `llm_kit` client. This enables testing against a `FakeLLM` without changing the stack.

**Skills and tools are peers.** Both sit at the same level. Skills are discovered and managed via `SkillManager` (see [[pages/entities/skills-system]]); tools are registered and executed via `ToolRegistry` (see [[pages/entities/tool-registry]]). The agent loop knows both, but neither module imports the other.

## Rationale

Strict layering keeps the codebase coherent as it scales. Every module has a clear scope — it can only depend downward, so circular dependencies are impossible. Testing is deterministic (layers below the one under test can be faked). New features (e.g., a new store backend) slot in without refactoring existing code.
