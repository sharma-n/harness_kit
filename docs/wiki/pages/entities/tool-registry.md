---
title: Tool Registry
category: entity
tags: [tools, permissions, execution, user-scoped]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/tools/registry.py, ROADMAP.md#M5]
status: current
---

# Tool Registry

Central registry for tool definitions and execution. Enforces user permissions and per-tool policies.

## Two Responsibilities

**Definitions:** `registry.definitions(user_id)` returns the tools this user is allowed to call, filtered by `PermissionStore`.

**Execution:** `registry.execute(user_id, tool_name, args)` invokes the tool with:
- Per-tool timeout (falls back to `agent.per_tool_timeout_s`)
- Per-user rate limiting (optional, per-tool)
- Permission re-check (defense-in-depth; even if definitions passed through, execute verifies access)
- Error handling: tool failures become `ToolResult(ok=False)`, not exceptions (see [[pages/concepts/tool-errors-as-observations]])

Returns a `ToolResult` with `ok`, `content`, and error details.

## Tool Kinds

**Native tools:** [[pages/entities/native-tools]] (remember_fact, forget_fact, list_facts, recall) + `read_skill`. Hard-coded in registry.

**MCP tools:** Discovered from MCP servers; wrapped as `Tool` objects and registered alongside native tools. See [[pages/entities/mcp-integration]].

## Per-Tool Policy

`ToolPolicy` in config defines optional per-tool overrides:
- `timeout_s` — override the global per-tool timeout
- `rate_limit_per_minute` — optional per-user rate limit for this tool
- `requires_approval` — HITL gate (see [[pages/decisions/hitl-approval-gates]])

## User Scoping

All operations verify `user_id`. `definitions()` filters by `PermissionStore`. `execute()` re-checks permissions (defense-in-depth).

## Determinism

Tool outputs are logged and can be deterministic (or not, depending on the tool). The registry doesn't hide non-determinism — it's up to the tool.
