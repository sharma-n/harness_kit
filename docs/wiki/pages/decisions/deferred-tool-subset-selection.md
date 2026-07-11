---
title: Deferred: Per-Turn Tool Subset Selection
category: decision
tags: [tools, scaling, latency, deferred, nice-to-haves]
created: 2026-07-11
updated: 2026-07-11
sources: [ROADMAP.md#Nice to haves, SPEC.md§6.3]
status: deferred
---

# Deferred: Per-Turn Tool Subset Selection

## Rationale for Deferral

Today the model is offered **every** [[pages/entities/tool-registry|allowed tool]], every iteration. [[pages/concepts/bottom-up-layering|SPEC §6.3]] warns this degrades at scale — 100 MCP tools per turn burns tokens and hurts LLM tool selection accuracy.

**But it's adoption-gated, not a blocking feature:** harness_kit ships 4 [[pages/entities/native-tools|native tools]] and zero bundled MCP tools. Tool count is whatever an operator wires up. It only bites a deployment connecting several large MCP servers.

**Shipping without it:** the first version is uncluttered, deterministic, and focuses the design on the [[pages/entities/agent-loop|core agent loop]] and [[pages/synthesis/memory-system-overview|memory system]].

## Constraint: Time-to-First-Token

harness_kit's core identity is latency — [[pages/entities/serving-layer|time-to-first-token]]. Selection must never add a synchronous round-trip *before* the first LLM call.

This rules out:
- **LLM router (two-pass):** first LLM call to pick tools, second to use them — adds delay before TTFT
- **Synchronous DB lookups:** (same problem)

## Recommended Three-Phase Design

### Phase 1: Threshold Gate (Build First)

```python
if len(allowed_tools) <= threshold:  # e.g., 25
    # Send all tools (today's behavior)
    send_all_tools()
else:
    # Engage selection
    selected = select_tools(query, top_k=15)
```

**Effect:** Inert until tool counts are large. The current 4-tool reality, the golden context test, and all early deployments stay untouched. Selection only engages above the threshold.

**Config:** `AgentConfig.tool_selection_threshold` (default 25, `None` to disable).

### Phase 2: Embedding-Based Retrieval (When a Many-Tool Deployment Appears)

1. **At startup** (`astart()` or `build()`): embed each tool's `name + description` into a small in-process vector index (same `Embedder` Protocol).
2. **Per turn:** rank tools by cosine similarity to the query (reuse the [[pages/entities/context-builder|context builder]]'s augmented message embedding). Take `top_N` tools (e.g., 15).
3. **Hold the subset** for the entire loop (don't re-select per iteration).

**Key:** Piggyback on the episodic query embedding (§6.4 already embeds the augmented user message). Selection adds zero latency — the embedding is computed for episodic retrieval; ranking tools is free overhead.

**Latency:** O(N·D) dot products (N = all tools, D = embedding dim), negligible vs. first LLM call latency.

### Phase 3: Progressive Disclosure Meta-Tool (Escalation if Quality Suffers)

If embedding-based ranking still has false negatives (model wants tools not in the top-N), expose:

- Core/native tools always
- `search_tools(query: str) -> list[Tool]` meta-tool

The model can invoke `search_tools` to pull in additional MCP tools on demand. Cost is paid lazily as an extra iteration (only when needed), not on every turn.

**Trade-off:** Biggest behavioral change (new tool, new loop iteration), hardest to keep deterministic. But zero hot-path latency — perfect for edge cases.

## Layering

Keep `[[pages/entities/tool-registry|ToolRegistry]]` answering only "what is this user *allowed*"; relevance ranking is a [[pages/entities/context-builder|context-assembly]] concern (`agent/context.py` or a small `agent/tool_selector.py`), sitting upstream of the [[pages/entities/context-budgeter|budgeter]]'s tier-0.

The tier-0 block (system message + tool defs) can then be smaller when selection engages, reducing context pressure.

## When to Implement

- **Now:** Operator with 4 native tools and 1-2 MCP servers — doesn't need this (won't trigger threshold).
- **Soon:** Operator with 50+ tools — threshold gate engages; embedding-based selection gives clean UX.
- **Later:** If embedding-based ranking has issues, add the meta-tool (but this is a v1.x extension, not v1.0).

## Determinism

Golden context test: stays untouched (tool count < threshold, selection is disabled).

New tests (if/when Phase 2 lands): tool selection must be deterministic per query (same embedding = same ranking = same subset) so test replay stays consistent.
