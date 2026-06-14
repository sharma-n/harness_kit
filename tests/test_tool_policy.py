"""Per-tool policy: timeout override + per-user rate limit (M10).

Both gates surface as ``ToolResult(ok=False)`` observations, never exceptions —
tool errors are observations (SPEC §5).
"""

from __future__ import annotations

import asyncio

from llm_kit import ToolCall, ToolDefinition

from agent_kit.config import ToolPolicy
from agent_kit.stores.memory_permissions import InMemoryPermissionStore
from agent_kit.tools.base import Tool
from agent_kit.tools.registry import ToolRegistry


def _tool(name: str, handler) -> Tool:
    return Tool(
        definition=ToolDefinition(name=name, description=f"{name} tool", parameters={}),
        handler=handler,
    )


def _registry(tools, *, policies=None, default=None) -> ToolRegistry:
    names = default if default is not None else {t.name for t in tools}
    perms = InMemoryPermissionStore(default_allowed=names)
    return ToolRegistry(tools, perms, per_tool_timeout_s=30.0, policies=policies)


async def test_per_tool_timeout_override_trips_before_global():
    async def slow(user_id: str, args: dict) -> str:
        await asyncio.sleep(0.1)
        return "done"

    registry = _registry(
        [_tool("slow", slow)],
        policies={"slow": ToolPolicy(timeout_s=0.01)},
    )
    result = await registry.execute("u", ToolCall(id="1", name="slow"))
    assert result.ok is False
    assert "timed out after 0.01s" in result.observation


async def test_tool_without_policy_uses_global_timeout():
    async def quick(user_id: str, args: dict) -> str:
        return "fast"

    registry = _registry([_tool("quick", quick)])  # no policies → global 30s
    result = await registry.execute("u", ToolCall(id="1", name="quick"))
    assert result.ok is True
    assert result.observation == "fast"


async def test_rate_limit_rejects_after_budget_exhausted():
    async def ping(user_id: str, args: dict) -> str:
        return "pong"

    registry = _registry(
        [_tool("ping", ping)],
        policies={"ping": ToolPolicy(rate_limit_per_minute=2)},
    )
    r1 = await registry.execute("u", ToolCall(id="1", name="ping"))
    r2 = await registry.execute("u", ToolCall(id="2", name="ping"))
    r3 = await registry.execute("u", ToolCall(id="3", name="ping"))

    assert r1.ok is True and r2.ok is True
    assert r3.ok is False
    assert "rate limit exceeded" in r3.observation


async def test_rate_limit_is_per_user():
    async def ping(user_id: str, args: dict) -> str:
        return "pong"

    registry = _registry(
        [_tool("ping", ping)],
        policies={"ping": ToolPolicy(rate_limit_per_minute=1)},
    )
    # alice exhausts her own bucket; bob still has his.
    assert (await registry.execute("alice", ToolCall(id="1", name="ping"))).ok is True
    assert (await registry.execute("alice", ToolCall(id="2", name="ping"))).ok is False
    assert (await registry.execute("bob", ToolCall(id="3", name="ping"))).ok is True
