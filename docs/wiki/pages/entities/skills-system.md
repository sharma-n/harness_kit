---
title: Skills System
category: entity
tags: [skills, discovery, filesystem, agentskills.io]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/skills/{loader,manager}.py, CLAUDE.md#Skills design decisions, ROADMAP.md#M6]
status: current
---

# Skills System

A skill is a filesystem-based instruction set for the agent. Skills are discovered at startup, indexed in-memory, and surfaced progressively to avoid token bloat.

## Format

A skill is a directory containing a `SKILL.md` file with YAML frontmatter:

```yaml
---
name: database_admin
description: Tools and instructions for database schema changes, backups, and monitoring.
allowed-tools: [query_database, run_migration, check_backup_status]  # optional
---
```

Followed by Markdown instructions for the agent. The directory can also contain `scripts/`, `references/`, and `assets/` subdirectories (following agentskills.io format).

## Key Decisions

- **Files on disk, never in database.** The filesystem is the source of truth. `SkillStore` only stores visibility grants (who can see which skills).

- **[[pages/concepts/progressive-disclosure|Progressive disclosure in three stages]]** — discovery (startup), activation (read_skill tool), reference loading (on demand).

- **Context assembly placement.** Skills block is tier-0 (never evicted) and appears between the dynamic system prompt and factual memory: `base_prompt → dynamic → skills_block → factual → episodic → summary`. See [[pages/entities/context-builder]].

- **`allowed-tools` is parsed, not auto-granted.** Stored in `SkillMeta` for operator inspection only. Granting a skill's tools requires explicit `PermissionStore.grant()` calls; see [[pages/decisions/skills-v1-v2-scaffolding]].

- **V2 scaffolding.** `SkillStore` Protocol is designed for per-user visibility overrides in the future; v1 uses global all-or-nothing (see [[pages/decisions/skills-v1-v2-scaffolding]]).

## Components

**`SkillMeta`** — Lightweight metadata loaded at startup: name, description, allowed-tools list, filesystem path. Full body is not loaded until needed.

**`loader.discover()`** — Scan configured directories for immediate subdirectories containing `SKILL.md`. Best-effort: malformed files are logged and skipped. Runs synchronously at startup.

**`SkillManager`** — In-memory index of discovered skills. Provides two methods:
- `metadata_block(allowed, header)` — lightweight block for system message (~50 tokens/skill)
- `read_body(name, allowed)` — full body for the `read_skill` tool (read from disk on demand, no cache)

## Integration

- Wired into [[pages/entities/context-builder]] as an optional field (absent when not configured).
- Accessed via `read_skill` native tool (see [[pages/entities/native-tools]]).
- Subject to [[pages/concepts/permission-model|permission gates]] via `SkillStore.allowed_skills()`.

## Script Execution

Skills can include `scripts/` directories per agentskills.io, but no shell tool exists today. Shell execution is deferred (ROADMAP item); adding it requires separate security decisions around sandboxing and approval.
