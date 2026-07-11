---
title: Skills V1-V2 Scaffolding
category: decision
tags: [skills, versioning, permissions, future-proof, grants]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/stores/base.py, src/harness_kit/skills/manager.py, CLAUDE.md#Skills design decisions]
status: current
---

# Skills V1-V2 Scaffolding

## Decision

The `SkillStore` Protocol is designed for per-user visibility overrides in v2, but v1 uses global all-or-nothing (all users see the same skills). The API is already ready for v2; only the adapter needs swapping.

## V1 (Current)

All users see all discovered skills. `SkillStore.allowed_skills(user_id)` always returns `None` (semantic: "no restriction"). The in-memory adapter is stateless; `SkillManager` filters by the returned `None` (which means all visible).

## V2 (Future)

A `SqliteSkillStore` adapter (or Redis variant) stores per-user grants. Calling `allowed_skills(user_id)` returns:

- `None` — user can read all skills (inherited default or explicit grant to everything)
- `set[str]` — user can read only these skill names

**API is unchanged.** `SkillManager.metadata_block()` and `read_body()` both accept the returned `allowed` set and filter identically. No change to `ContextBuilder` or `read_skill` handler.

## `allowed-tools` Is Not Auto-Granted

When a skill is defined with `allowed-tools: [tool1, tool2]`, those tools are **stored in `SkillMeta.allowed_tools`** for operator inspection only. They are **not automatically granted** to users who read the skill.

**Why:** Skill tooling is a separate authorization decision from skill visibility. Granting a skill's tools requires explicit `PermissionStore.grant(user_id, [tool1, tool2])` calls by an operator.

**V2 extension (future):** A new optional `auto_grant_tools: true` policy (mirroring MCP's `auto_allow`) would automatically grant listed tools to users who are allowed to read the skill. This is deferred because it requires additional operator workflow planning.

## Current Workflow

1. Deploy with skills discovered from disk.
2. Operators update the default allowlist in `config.yaml` or via `PermissionStore.grant()` to allow specific tools.
3. Operators manually grant skill visibility if v2 per-user grants are needed (not in v1).
4. Users read skills via `read_skill` and receive tool observations.

## Scaling to V2

To adopt per-user skill grants:

1. Implement `SqliteSkillStore` adapter (or Redis variant).
2. Update `config.yaml` to select the adapter.
3. No code changes to `SkillManager`, `ContextBuilder`, or `read_skill` — the Protocol is already ready.
4. Operators use the adapter's admin interface to grant skills per user.

## Permissions Layering

Skills, tools, and permissions form a three-layer stack:

1. **Skill visibility** — `SkillStore.allowed_skills()` (v2 per-user, v1 all-or-nothing)
2. **Skill tool listing** — `SkillMeta.allowed_tools` (operator inspection; not auto-granted)
3. **Tool execution** — `PermissionStore` (per-user + per-tool policies; two-check defense-in-depth)

Each layer is independent; a user seeing a skill and its recommended tools still must have explicit execute permission for those tools.
