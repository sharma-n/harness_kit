---
title: Permission Model
category: concept
tags: [tools, permissions, user-scoped, authorization, defense-in-depth]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/stores/base.py, src/harness_kit/tools/registry.py, CLAUDE.md#Multi-user is foundational, ROADMAP.md#M5]
status: current
---

# Permission Model

Tool execution is gated by a two-layer permission check: definitions are filtered at assembly time, and every execution re-checks authorization (defense-in-depth).

## Default Allowlist

`PermissionStore` has a global default allowlist (configured in `config.yaml`), applied to all users by default. The native tools (see [[pages/entities/native-tools]]) are always in this list so all users can call them. MCP server tools can be in the default via `auto_allow: true` or omitted and granted per-user.

## Per-User Overrides

Users can have explicit grants or restrictions stored in `PermissionStore`:

- **Grant:** Add tools to a user's allowlist (subset of all tools, including MCP discoveries).
- **Deny:** Restrict a user's toolset (remove from default).
- **Sentinel row:** Default permissions stored as `user_id='__default__'` (SQLite/SQL adapters).

## Two-Check Pattern

1. **Definitions check:** At context assembly, `ToolRegistry.definitions(user_id)` returns only tools the user is allowed to use. These are sent to the model in the system message, so the model never "sees" tools it can't call.

2. **Execute check:** When `ToolRegistry.execute(user_id, tool_name, args)` is called, it re-checks `PermissionStore` before invoking. Even if a tool slipped through definitions (a bug or race), execution re-verifies authorization.

The redundancy is intentional: defense-in-depth so a single code path failure doesn't leak permissions.

## Skill Tool Permissions

`read_skill` (see [[pages/entities/native-tools]]) is re-checked at execution time via `SkillStore.allowed_skills(user_id)`. If the user isn't allowed to read a skill, the tool handler returns an error (fed back to the model as a [[pages/concepts/tool-errors-as-observations|tool-error observation]], not an exception).

## Rate-Limiting Integration

Per-tool rate limits (see [[pages/decisions/rate-limiting-in-process]]) are checked *after* permissions but *before* the actual tool call. Rate-limited tools emit `ToolResult(ok=False)` with a "rate limit exceeded" message.

## Approval Gates

Tools requiring human approval (see [[pages/decisions/hitl-approval-gates]]) enter a separate flow: the loop emits `ToolApprovalRequired` *before* executing, and the user must approve/deny via the serving layer (WS or SSE) before execution proceeds.

## User Scoping Guarantee

All permission checks verify `user_id`. A user cannot execute a tool granted only to another user, and [[pages/entities/tool-registry]] ensures the model is never offered a tool it can't call.
