---
title: Context Builder
category: entity
tags: [context, memory, assembly, budgeting]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/agent/context.py, CLAUDE.md#Key abstractions to know]
status: current
---

# Context Builder

Assembles the five context sources (SPEC §6.2) into the provider's message list, in order of model attention.

## The Five Sources

**Tier 0 (system message, never drops):**
- Identity and rules (dynamic system prompt per user and conversation)
- Factual profile (knowledge about this user)
- Episodic hits (relevant conversation memories)
- Summary (rolled-up older turns from the working buffer)
- Tool definitions (what the model can call)

**Tier 1 (working buffer):**
- Oldest → newest turns in this conversation (verbatim)

**Tier 2 (current message):**
- The user's latest message

The ordering matters: earlier in the message list has higher model attention. Factual profile comes before episodic (stable, trusted facts before episodic hunches). Episodic comes before the working buffer (recent conversation context grounds fresh memories). The summary (of earlier turns) comes after the system prompt but before the working buffer.

## User Scoping

Every fetch is scoped to `user_id`:
- Working buffer loads from `SessionStore.load(conversation_id, user_id)` (raises `UnauthorizedError` if wrong user)
- Factual profile from `FactualMemory.get(user_id)`
- Episodic hits from `EpisodicMemory.retrieve(user_id, ...)`
- Tools from `ToolRegistry.definitions(user_id)` (user's allowed tools only)

See [[pages/concepts/multi-user-scoping]] for the multi-user enforcement pattern.

## Budgeting

After gathering the five sources, the `ContextBudgeter` (see [[pages/entities/context-budgeter]]) evicts by tier to fit the model's context window. The builder passes the budgeted sources to the `AssembledContext`, which is then handed to the [[pages/entities/agent-loop]].

## Skills Integration

When `skill_manager` and `skill_store` are configured, a skills block (tier-0, never evicted) is injected between the dynamic system prompt and the factual profile. This lists available skills the model can call via the `read_skill` native tool.

## Deterministic Assembly

The builder is deterministic given its inputs — for testing, the §6.6 worked example from SPEC is a golden test of the exact message list and order.
