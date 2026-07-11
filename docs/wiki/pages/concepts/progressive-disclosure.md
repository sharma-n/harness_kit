---
title: Progressive Disclosure
category: concept
tags: [skills, loading, context, tokens, startup]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/skills/{loader,manager}.py, CLAUDE.md#Skills design decisions]
status: current
---

# Progressive Disclosure

Three-stage loading strategy for skills, trading off latency (startup time), context size, and availability.

## Three Stages

**1. Startup discovery** — `loader.discover(cfg.skills.paths)` scans directories and loads metadata only (name, description). Runs synchronously; cheap (filesystem I/O, no network). Result: ~50 tokens/skill in the system message. The agent learns skill names and high-level purposes, enough to know which skills might be relevant.

**2. Activation (on-demand)** — When the agent calls `read_skill(name)`, the full `SKILL.md` body is read from disk and returned as a tool observation. No in-process body cache — body is read fresh on each call, allowing operators to update skill instructions without restarting the service.

**3. Reference loading** — Skill instructions can tell the agent to read additional files (e.g., `references/api_endpoints.md`) using existing file-reading tools. Operators can compose skills with external reference materials without special plumbing.

## Rationale

- **Startup is fast.** Metadata-only discovery runs synchronously during `service.build()` — no blocking on network or disk, no async ceremony in the startup path.

- **Context bloat is minimal.** 50 tokens/skill is acceptable; full skill bodies (often 500+ tokens) are only sent when the agent requests them.

- **Skills are live.** No restart needed to update a skill — operators edit the file, and the next `read_skill` call returns the new body. Changes are visible within seconds.

- **Flexibility.** Skill instructions can compose other skills, reference external docs, or iterate — the three-stage model supports all without new tooling.

## Integration with Context

The metadata block (stage 1) is assembled into tier-0 of the context (via [[pages/entities/context-builder]]) alongside [[pages/entities/tool-registry|tool definitions]]. It never evicts, so the agent always knows which skills are available, but doesn't pay the token cost of their full bodies until explicitly requested.

Full bodies (stage 2) come in as [[pages/concepts/tool-errors-as-observations|tool observations]] and compete for space in the working buffer (tier-3), subject to [[pages/entities/context-budgeter|token-driven eviction]].
