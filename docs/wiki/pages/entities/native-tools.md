---
title: Native Tools
category: entity
tags: [tools, memory, native, hardcoded]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/tools/native.py, CLAUDE.md#HITL tool approval, ROADMAP.md#M3]
status: current
---

# Native Tools

Five hardcoded tools for memory access and skill discovery, built into every harness_kit service. No configuration needed; they always appear in the tool registry.

## Factual Memory Tools

**`remember_fact`** — Store or update a durable user fact (preferences, skills, constraints, location, habits). Key-value pairs. Overwrites on re-call (also serves as update). Steers users toward [[pages/entities/factual-memory]] for stable attributes rather than ephemeral discussion context.

**`forget_fact`** — Delete a fact by key. Idempotent; returns "no such fact" if it doesn't exist.

**`list_facts`** — Enumerate all currently-stored facts for the user (no arguments).

## Episodic Memory Tools

**`recall`** — Search past conversations by semantic similarity. Returns top-k hits from [[pages/entities/episodic-memory]], each prefixed with `[conversation_id]` (allows targeting `forget_memory` calls). Unlike the auto-injected episodic context, this is explicit — the model must decide to search.

**`forget_memory`** — Delete all episodic embeddings for a conversation (e.g., after recall returns a conversation_id the user wants to erase). Irreversible. Seeded into default allowlist with `requires_approval: true` in config.yaml — a [[pages/decisions/hitl-approval-gates]] guard prevents accidental deletion.

## Skill Tool

**`read_skill`** — Fetch the full `SKILL.md` body for a named skill. Part of the [[pages/concepts/progressive-disclosure]] pattern: skill names are in the system message; `read_skill` loads the full instructions on-demand. Re-checks `SkillStore.allowed_skills(user_id)` at execution time (defense-in-depth, per [[pages/concepts/permission-model]]).

## Permission Seeding

All native tools are in the `PermissionStore` default allowlist (so all users can call them by default). `forget_memory` additionally has `requires_approval: true` in `config.yaml` to add a [[pages/decisions/hitl-approval-gates]] gate.

## Determinism

All native tools are deterministic for their given input (no randomness, no time-based variance). Their outputs are logged and can be replayed in traces.

## Integration

Wired by [[pages/entities/tool-registry]] at startup and executed via the same [[pages/entities/agent-loop]] error-handling path as MCP tools.
