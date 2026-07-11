---
title: MCP Integration
category: entity
tags: [tools, mcp, servers, discovery, namespacing]
created: 2026-07-11
updated: 2026-07-11
sources: [src/harness_kit/tools/mcp.py, src/harness_kit/config/schema.py, CLAUDE.md#MCP gotchas, ROADMAP.md#M5]
status: current
---

# MCP Integration

Bring-your-own MCP servers (Model Context Protocol). Operators configure one or more servers; harness_kit discovers their tools at startup and surfaces them through the [[pages/entities/agent-loop]] alongside [[pages/entities/native-tools]].

## Architecture

**`MCPServerClient`** — Live connection to one MCP server over a configured transport (stdio, HTTP, SSE). Handles `connect()`, `discover()` (list tools), and `call(tool_name, args)`. Lazy-imports the `mcp` SDK so the optional extra is only required when MCP is actually configured.

**`MCPManager`** — Connects all configured servers concurrently at startup (`astart()`). Best-effort: a server that fails to connect or discover is logged and skipped — one bad server never crashes the service. Aggregates all tools from all servers into a single list.

## Tool Discovery & Namespacing

Tools are discovered once at startup and wrapped as [[pages/entities/tool-registry#Tool Kinds|Tool objects]]. To avoid name collisions across servers, tools are namespaced: `{server_name}__{tool_name}` (double-underscore; a single `_` or `.` can appear in server/tool names).

The wrapped tool's handler de-namespaces before calling the server — the MCP server always receives the original tool name.

## Auto-Allow Policy

Each MCP server has an optional `auto_allow: bool` flag (default false). When true, its discovered tools are automatically added to the default allowlist in `PermissionStore`. When false, operators must explicitly grant them via `PermissionStore.grant()`.

## Permissions & Execution

MCP tools are subject to the same [[pages/concepts/permission-model]] as native tools:

- Definitions are filtered by user's allowed toolset at assembly time.
- Re-check on execute (defense-in-depth).
- Per-tool policies (timeout, rate limit, requires_approval) apply identically.

## Error Handling

If an MCP tool call returns `isError=true`, the handler raises a `RuntimeError` so [[pages/entities/tool-registry]] records it as `ToolResult(ok=False)` and feeds the error message back to the model as an observation (per [[pages/concepts/tool-errors-as-observations]]).

## Transports

- **stdio** — Local subprocess with command + optional args (e.g., `python -m myserver`).
- **HTTP** (streamable HTTP) — Remote HTTP endpoint supporting MCP's streamable protocol.
- **SSE** — Remote server using Server-Sent Events.

All are async; transport client lifecycle is managed by an `AsyncExitStack` held for the app's lifetime (not one-shot).

## Limitations

The MCP spec supports resources and sampling; harness_kit currently uses tools only. Resources are a future extension.

## Integration with Context & Serving

MCP tool definitions are assembled into the context's tier-0 tool-defs block, evicted together with other tool-def bloat if needed (rare — tier-0 almost never drops). MCP tool calls stream through the same [[pages/entities/agent-loop]] and [[pages/serving/wire.py]] as native tools, so clients see no distinction between tool kinds.
