"""MCP integration tests — network-free (no subprocess/socket; fakes only).

Two levels:
  - ``MCPServerClient`` against a fake ``ClientSession`` — exercises the real
    namespacing, handler routing, and ``isError`` → raise mapping.
  - ``MCPManager`` / ``AgentService`` against a ``FakeMcpClient`` — exercises
    best-effort connect, ``auto_allow``, registration, and an end-to-end turn.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from harness_kit.config import HarnessKitConfig
from harness_kit.stores.memory_permissions import InMemoryPermissionStore
from harness_kit.tools.base import Tool
from harness_kit.tools.mcp import MCPManager, MCPServerClient, namespaced
from harness_kit.tools.registry import ToolRegistry
from llm_kit import ToolCall

from tests.conftest import ScriptedTurn, make_service, tc

# --- fakes -------------------------------------------------------------------


class FakeSession:
    """Mimics the slice of ``mcp.ClientSession`` that ``MCPServerClient`` uses."""

    def __init__(self, tools: list[dict], result_text: str = "ok", is_error: bool = False):
        self._tools = tools
        self._result_text = result_text
        self._is_error = is_error
        self.calls: list[tuple[str, dict]] = []

    async def list_tools(self):
        specs = [
            SimpleNamespace(name=t["name"], description=t.get("description", ""), inputSchema=t.get("schema"))
            for t in self._tools
        ]
        return SimpleNamespace(tools=specs)

    async def call_tool(self, name: str, arguments: dict[str, Any]):
        self.calls.append((name, arguments))
        return SimpleNamespace(
            content=[SimpleNamespace(text=self._result_text)],
            isError=self._is_error,
        )


class FakeMcpClient:
    """A connected MCP server, fully in-process (implements ``McpClient``)."""

    def __init__(self, name: str, tool_names: list[str], *, auto_allow: bool = False,
                 fail_connect: bool = False, call_delay_s: float = 0.0):
        self.name = name
        self.auto_allow = auto_allow
        self._tool_names = tool_names
        self._fail_connect = fail_connect
        self._call_delay_s = call_delay_s
        self.closed = False
        self.calls: list[tuple[str, dict]] = []

    async def connect(self) -> None:
        if self._fail_connect:
            raise RuntimeError(f"{self.name}: cannot connect")

    async def discover(self) -> list[Tool]:
        from llm_kit import ToolDefinition

        tools: list[Tool] = []
        for tname in self._tool_names:
            tools.append(
                Tool(
                    definition=ToolDefinition(
                        name=namespaced(self.name, tname),
                        description=f"{tname} on {self.name}",
                        parameters={"type": "object", "properties": {}},
                    ),
                    handler=self._make_handler(tname),
                )
            )
        return tools

    def _make_handler(self, tool_name: str):
        async def handler(user_id: str, args: dict) -> str:
            return await self.call(tool_name, args)

        return handler

    async def call(self, tool_name: str, arguments: dict) -> str:
        if self._call_delay_s:
            await asyncio.sleep(self._call_delay_s)
        self.calls.append((tool_name, arguments))
        return f"{self.name}:{tool_name}:{arguments}"

    async def aclose(self) -> None:
        self.closed = True


# --- MCPServerClient (real wrapping logic over a fake session) ---------------


async def test_server_client_discover_namespaces_and_routes():
    server = SimpleNamespace(name="files", auto_allow=False)
    client = MCPServerClient.__new__(MCPServerClient)
    client.name = "files"
    client.auto_allow = False
    session = FakeSession(tools=[{"name": "read", "description": "read a file"}])
    client._session = session
    client._stack = None

    tools = await client.discover()
    assert [t.name for t in tools] == ["files__read"]

    out = await tools[0].handler("alice", {"path": "/x"})
    # The handler must call the *un-namespaced* tool name on the session.
    assert session.calls == [("read", {"path": "/x"})]
    assert out == "ok"


async def test_server_client_error_result_raises_then_registry_marks_failure():
    client = MCPServerClient.__new__(MCPServerClient)
    client.name = "files"
    client.auto_allow = False
    client._session = FakeSession(
        tools=[{"name": "read"}], result_text="boom", is_error=True
    )
    client._stack = None
    tools = await client.discover()

    perms = InMemoryPermissionStore(default_allowed={"files__read"})
    registry = ToolRegistry(tools, perms)
    execution = await registry.execute("alice", ToolCall(id="c1", name="files__read", arguments={}))
    assert execution.ok is False
    assert "boom" in execution.observation


# --- MCPManager --------------------------------------------------------------


async def test_manager_skips_failed_server_keeps_others():
    good = FakeMcpClient("good", ["a"])
    bad = FakeMcpClient("bad", ["b"], fail_connect=True)
    manager = MCPManager([], clients=[good, bad])

    tools, auto_allowed = await manager.start()
    assert [t.name for t in tools] == ["good__a"]
    assert auto_allowed == set()
    # The failed server is aclosed and not retained.
    assert bad.closed is True

    await manager.aclose()
    assert good.closed is True


async def test_manager_auto_allow_collects_namespaced_names():
    client = FakeMcpClient("trusted", ["x", "y"], auto_allow=True)
    manager = MCPManager([], clients=[client])
    tools, auto_allowed = await manager.start()
    assert {t.name for t in tools} == {"trusted__x", "trusted__y"}
    assert auto_allowed == {"trusted__x", "trusted__y"}


# --- AgentService end-to-end -------------------------------------------------


async def test_astart_auto_allow_makes_tool_callable_end_to_end():
    cfg = HarnessKitConfig()  # empty default_allowed
    mcp_tool = namespaced("trusted", "echo")
    turns = [
        ScriptedTurn(tool_calls=[tc("c1", mcp_tool, msg="hi")]),
        ScriptedTurn(text_chunks=["done"]),
    ]
    client = FakeMcpClient("trusted", ["echo"], auto_allow=True)
    service, _ = make_service(cfg, turns, mcp_clients=[client])
    await service.astart()

    # auto_allow folded the namespaced name into the default allowlist.
    assert mcp_tool in await service.stores.permissions.allowed_tools("newuser")

    results = [
        e async for e in service.agent.run_turn("newuser", "conv1", "use the tool")
    ]
    tool_results = [e for e in results if getattr(e, "name", None) == mcp_tool]
    assert any(getattr(e, "ok", None) is True for e in tool_results)
    assert client.calls == [("echo", {"msg": "hi"})]
    await service.aclose()


async def test_astart_without_auto_allow_does_not_grant():
    cfg = HarnessKitConfig()
    client = FakeMcpClient("guarded", ["danger"], auto_allow=False)
    service, _ = make_service(cfg, mcp_clients=[client])
    await service.astart()

    # Tool is registered but not in any user's allowlist by default.
    assert namespaced("guarded", "danger") not in await service.stores.permissions.allowed_tools("u")
    defs = await service.registry.definitions("u")
    assert namespaced("guarded", "danger") not in {d.name for d in defs}
    await service.aclose()


async def test_mcp_tool_per_tool_timeout_marks_failure():
    cfg = HarnessKitConfig()
    cfg.agent.per_tool_timeout_s = 0.05
    client = FakeMcpClient("slow", ["wait"], auto_allow=True, call_delay_s=1.0)
    service, _ = make_service(cfg, mcp_clients=[client])
    await service.astart()

    execution = await service.registry.execute(
        "u", ToolCall(id="c1", name=namespaced("slow", "wait"), arguments={})
    )
    assert execution.ok is False
    assert "timed out" in execution.observation
    await service.aclose()
