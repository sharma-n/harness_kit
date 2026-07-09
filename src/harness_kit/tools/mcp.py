"""MCP client surface (SPEC §8) — connect to operator-supplied MCP servers.

harness_kit does not ship a tool library; it lets a deployment **bring its own** MCP
servers (local stdio subprocesses or remote HTTP/SSE) and surfaces their tools to
the model through the existing agent loop. A discovered MCP tool is wrapped as a
plain ``Tool`` — the same abstraction native tools use — so ``ToolRegistry`` treats
both identically (per-user allowlist filtering, timeout, error-as-observation).

Tools are namespaced ``{server}__{tool}`` to avoid collisions across servers. The
double underscore is provider-safe (a dot fails OpenAI's tool-name validation) and
collision-safe when a server or tool name itself contains single underscores.

The concrete ``mcp`` SDK is imported lazily inside ``connect()`` so importing this
module without the optional ``mcp`` extra never fails (mirrors ``stores/stubs.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Protocol

from llm_kit import ToolDefinition

from harness_kit.config import McpServerConfig, McpTransport
from harness_kit.tools.base import Tool

logger = logging.getLogger(__name__)

NAMESPACE_SEP = "__"


def namespaced(server: str, tool: str) -> str:
    return f"{server}{NAMESPACE_SEP}{tool}"


class McpClient(Protocol):
    """One MCP server connection — the per-server seam tests fake against."""

    name: str
    auto_allow: bool

    async def connect(self) -> None: ...

    async def discover(self) -> list[Tool]:
        """Return this server's tools, namespaced by server."""
        ...

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Invoke an *un-namespaced* tool name on this server."""
        ...

    async def aclose(self) -> None: ...


class MCPServerClient:
    """Live connection to one configured MCP server over its transport."""

    def __init__(self, server: McpServerConfig) -> None:
        self.name = server.name
        self.auto_allow = server.auto_allow
        self._server = server
        self._session: Any = None  # mcp.ClientSession once connected
        self._stack: Any = None  # contextlib.AsyncExitStack holding transport+session

    async def connect(self) -> None:
        # Lazy import: only deployments that actually configure MCP servers need the
        # optional ``mcp`` extra installed.
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.sse import sse_client
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamablehttp_client

        stack = contextlib.AsyncExitStack()
        srv = self._server
        if srv.transport is McpTransport.STDIO:
            if not srv.command:
                raise ValueError(f"mcp server {srv.name!r}: stdio transport requires 'command'")
            params = StdioServerParameters(command=srv.command, args=list(srv.args))
            read, write = await stack.enter_async_context(stdio_client(params))
        elif srv.transport is McpTransport.HTTP:
            if not srv.url:
                raise ValueError(f"mcp server {srv.name!r}: http transport requires 'url'")
            # streamable HTTP yields a 3-tuple (read, write, get_session_id).
            read, write, _ = await stack.enter_async_context(streamablehttp_client(srv.url))
        elif srv.transport is McpTransport.SSE:
            if not srv.url:
                raise ValueError(f"mcp server {srv.name!r}: sse transport requires 'url'")
            read, write = await stack.enter_async_context(sse_client(srv.url))
        else:  # pragma: no cover - exhaustive over the enum
            raise ValueError(f"mcp server {srv.name!r}: unknown transport {srv.transport!r}")

        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._session = session
        self._stack = stack

    async def discover(self) -> list[Tool]:
        result = await self._session.list_tools()
        tools: list[Tool] = []
        for spec in result.tools:
            tools.append(
                Tool(
                    definition=ToolDefinition(
                        name=namespaced(self.name, spec.name),
                        description=spec.description or "",
                        parameters=spec.inputSchema or {"type": "object", "properties": {}},
                    ),
                    handler=self._make_handler(spec.name),
                )
            )
        return tools

    def _make_handler(self, tool_name: str):
        # Closes over the *original* (un-namespaced) name so the call forwards
        # correctly; the namespaced name only ever lives in the registry/provider.
        async def handler(user_id: str, args: dict[str, Any]) -> str:
            return await self.call(tool_name, args)

        return handler

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> str:
        result = await self._session.call_tool(tool_name, arguments)
        text = _result_text(result)
        if getattr(result, "isError", False):
            # Raise so ToolRegistry records ToolResult(ok=False) and feeds the error
            # back to the model as an observation (tool errors are not exceptions).
            raise RuntimeError(text or f"mcp tool {tool_name!r} returned an error")
        return text

    async def aclose(self) -> None:
        if self._stack is not None:
            with contextlib.suppress(Exception):
                await self._stack.aclose()
            self._stack = None
            self._session = None


def _result_text(result: Any) -> str:
    """Join the text blocks of a CallToolResult into one observation string."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
    return "\n".join(parts)


class MCPManager:
    """Connects all configured MCP servers and aggregates their tools.

    Best-effort: a server that fails to connect or discover within
    ``startup_timeout_s`` is logged and skipped — one bad server never crashes the
    service (same posture as the idle sweeper). ``clients`` is the test seam: pass
    fakes to exercise discovery/namespacing/permissions without real transports.
    """

    def __init__(
        self,
        servers: list[McpServerConfig],
        *,
        startup_timeout_s: float = 30.0,
        clients: list[McpClient] | None = None,
    ) -> None:
        self._startup_timeout_s = startup_timeout_s
        if clients is not None:
            self._clients = list(clients)
        else:
            self._clients = [MCPServerClient(s) for s in servers]
        self._connected: list[McpClient] = []

    async def start(self) -> tuple[list[Tool], set[str]]:
        """Connect + discover across all servers.

        Returns ``(tools, auto_allowed)`` where ``auto_allowed`` is the set of
        namespaced tool names belonging to ``auto_allow`` servers.
        """
        tools: list[Tool] = []
        auto_allowed: set[str] = set()
        for client in self._clients:
            try:
                async with asyncio.timeout(self._startup_timeout_s):
                    await client.connect()
                    discovered = await client.discover()
            except Exception:
                logger.warning(
                    "mcp server %r failed to connect/discover; skipping", client.name,
                    exc_info=True,
                )
                with contextlib.suppress(Exception):
                    await client.aclose()
                continue
            self._connected.append(client)
            tools.extend(discovered)
            if client.auto_allow:
                auto_allowed.update(t.name for t in discovered)
            logger.info("mcp server %r connected: %d tool(s)", client.name, len(discovered))
        return tools, auto_allowed

    async def aclose(self) -> None:
        for client in self._connected:
            with contextlib.suppress(Exception):
                await client.aclose()
        self._connected = []
