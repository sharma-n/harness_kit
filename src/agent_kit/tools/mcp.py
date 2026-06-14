"""MCP client surface (SPEC §8) — stubbed for this milestone.

The real client connects to configured MCP servers, discovers tools, and invokes
them; tools are namespaced by server (``{server}.{tool}``) to avoid collisions.
The Protocol is defined now so the registry can integrate MCP tools without
change later; the concrete client raises ``NotImplementedError``.
"""

from __future__ import annotations

from typing import Any, Protocol

from agent_kit.config import McpServerConfig
from agent_kit.tools.base import Tool


class McpClient(Protocol):
    async def connect(self) -> None: ...

    async def discover(self) -> list[Tool]:
        """Return discovered tools, namespaced by server."""
        ...

    async def call(self, name: str, arguments: dict[str, Any]) -> str: ...

    async def aclose(self) -> None: ...


class StubMcpClient:
    """Placeholder until MCP integration lands (next milestone)."""

    def __init__(self, servers: list[McpServerConfig]) -> None:
        self._servers = servers

    async def connect(self) -> None:
        raise NotImplementedError("MCP client not implemented yet")

    async def discover(self) -> list[Tool]:
        raise NotImplementedError("MCP client not implemented yet")

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        raise NotImplementedError("MCP client not implemented yet")

    async def aclose(self) -> None:
        return None
