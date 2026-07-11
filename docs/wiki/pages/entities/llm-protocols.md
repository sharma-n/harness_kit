---
title: LLM Protocols
category: entity
tags: [abstraction, protocol, llm_kit, testability]
created: 2026-07-11
updated: 2026-07-11
sources: [CLAUDE.md#The non-negotiable rule: strict bottom-up layering, src/harness_kit/llm.py]
status: current
---

# LLM Protocols

Thin `LLM` and `Embedder` Protocols over `llm_kit` that abstract the concrete client from the rest of the stack.

## Purpose

Every layer above [[pages/concepts/bottom-up-layering#the-stack]] (stores, memory, agent, serving) depends on these Protocols, not on the concrete `llm_kit.LLMClient` or `llm_kit.Embedder`. This **Protocol-not-implementation** approach:

- Enables testing with a `FakeLLM` / `FakeEmbedder` without rebuilding the entire stack
- Allows per-conversation model switching without changing the agent loop (see [[pages/decisions/per-conversation-model-switching]])
- Keeps llm_kit as an implementation detail, not a pervasive dependency

## The Protocols

**`LLM` Protocol** (in `src/harness_kit/llm.py`):
- `invoke_stream(prompt, tools, model=None)` → async generator of `TextChunk`s and a `StreamEnd` with response metadata and tool calls
- Mirrors `llm_kit.LLMClient.invoke_stream` but is abstracted

**`Embedder` Protocol**:
- `embed(text, model=None)` → embedding vector
- `embed_batch(texts)` → list of vectors
- Mirrors `llm_kit.Embedder` but is abstracted

## Composition

`service.py` is the only place that instantiates concrete `llm_kit.LLMClient` and wrappers. The [[pages/entities/service-composition-root]] passes these as implementations of the Protocols to the layers below, which never know about llm_kit directly.

## Cost Tracking

The `llm_kit.TokenUsage` (input/output token counts) flows through every LLM invocation, feeding [[pages/entities/telemetry]] for cost accounting (see [[pages/decisions/vendor-neutral-telemetry-seam]]).
