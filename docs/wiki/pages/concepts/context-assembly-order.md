---
title: Context Assembly Order
category: concept
tags: [context, ordering, attention, model-behavior]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/agent/context.py, SPEC.md#6.2]
status: current
---

# Context Assembly Order

The order in which context sources appear in the message list materially affects model behavior. Harness Kit uses a fixed, deliberate order:

```
[ system ]   identity + rules
             ↳ dynamic system prompt (per user, per conversation)
             ↳ skills block (if configured)
             ↳ factual block (what we know about this user)
             ↳ episodic block (relevant memories)
             ↳ summary block (earlier turns, rolled-up)
[ user/assistant … ]  working buffer, oldest→newest, verbatim
[ user ]     the current message
```

## Rationale

**Factual before episodic** — Stable, high-confidence facts from the profile come before episodic hunches. If the profile says "the user is a senior engineer," that truth anchors before we consider "they asked about Rust three conversations ago."

**Episodic before buffer** — Recent conversation context grounds fresher memories. The model reads the buffer (this conversation verbatim), which gives it context for understanding what specific past episodic hit is relevant.

**Summary before buffer** — The summary (rolled-up earlier turns) comes after the system message but before the working buffer. This bridges the old and new: earlier context → recent summary → current turn → new message.

**Current message last** — The user's latest message goes last so it has maximum attention (recency bias in LLMs, and it's literally at the end of the context window).

## Testing

The golden context test (`tests/test_context.py`) asserts the exact message list and order for a worked example. Changing the order requires deliberate, tested change — no silent reordering.

## Budgeting Integration

The [[pages/entities/context-budgeter]] respects this order when evicting: it never drops tier-0 (system + current + tools), and it evicts from the lowest-priority tiers (episodic, then buffer, then factual) before overflowing.
