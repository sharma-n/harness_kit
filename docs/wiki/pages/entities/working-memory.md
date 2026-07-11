---
title: Working Memory
category: entity
tags: [memory, buffer, turns, session-state]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/memory/working.py, CLAUDE.md#Memory design decisions]
status: current
---

# Working Memory

Holds the verbatim transcript of the current conversation — the turns (user message, assistant response) — in a session-scoped buffer.

## Lifecycle

- **Load:** On each turn, `WorkingMemory.load(conversation_id, user_id)` fetches the buffer from [[pages/entities/stores-overview]] (session store). Filtered by `user_id` for security.
- **Append:** After a turn completes (streaming ends), the completed turn is appended to the buffer (synchronous).
- **Evict:** When the buffer exceeds `buffer_token_budget`, the oldest turns are summarized via [[pages/decisions/rolling-summary-rollover]] and dropped.

## Token-Budget-Driven Rollover

When the buffer's token count exceeds the configured ceiling, the oldest turns are selected for rollover: they are passed to the LLM with a `RolledSummary` response model, the summary is folded into the summary block, and the original turns are dropped from the buffer.

This is **token-driven, not turn-count driven** — a buffer with 10 long turns might exceed budget while a buffer with 50 short turns stays within it. It scales naturally with turn size.

See [[pages/decisions/rolling-summary-rollover]] for details and rationale.

## Integration with Context Assembly

The [[pages/entities/context-builder]] loads the buffer and passes it to the [[pages/entities/context-budgeter]], which may further trim the buffer to fit the model window (if higher-tier sources also need space). The buffer—oldest → newest—appears verbatim in the context, preserving the conversation flow.

## Per-Conversation Model Switching

The buffer also stores `model_name: str | None`, allowing [[pages/decisions/per-conversation-model-switching]]. On load, the agent loop checks for a model override and swaps the LLM if needed.

## Scoping & Multi-User

Always loaded with `user_id`, raising `UnauthorizedError` if the caller tries to access another user's conversation.
