"""Per-user tool enforcement at both selection and execution (SPEC §15)."""

from __future__ import annotations

from harness_kit.stores.memory_permissions import InMemoryPermissionStore
from harness_kit.tools.base import Tool
from harness_kit.tools.registry import ToolRegistry
from llm_kit import ToolCall, ToolDefinition


def _echo_tool(name: str) -> Tool:
    async def handler(user_id: str, args: dict) -> str:
        return f"{name} ran for {user_id}"

    return Tool(
        definition=ToolDefinition(name=name, description=f"{name} tool", parameters={}),
        handler=handler,
    )


def _registry(default: set[str]) -> tuple[ToolRegistry, InMemoryPermissionStore]:
    perms = InMemoryPermissionStore(default_allowed=default)
    registry = ToolRegistry([_echo_tool("alpha"), _echo_tool("beta")], perms)
    return registry, perms


async def test_definitions_filtered_per_user():
    registry, perms = _registry(default={"alpha"})
    await perms.grant("poweruser", {"alpha", "beta"})

    names_default = {d.name for d in await registry.definitions("normaluser")}
    names_power = {d.name for d in await registry.definitions("poweruser")}

    assert names_default == {"alpha"}
    assert names_power == {"alpha", "beta"}


async def test_execute_denies_unpermitted_tool():
    registry, _ = _registry(default={"alpha"})
    # Model emits a call to a tool this user is not allowed to use.
    result = await registry.execute("normaluser", ToolCall(id="1", name="beta"))
    assert result.ok is False
    assert "not permitted" in result.observation


async def test_execute_runs_permitted_tool():
    registry, _ = _registry(default={"alpha"})
    result = await registry.execute("normaluser", ToolCall(id="1", name="alpha"))
    assert result.ok is True
    assert result.observation == "alpha ran for normaluser"


async def test_tool_error_becomes_observation_not_exception():
    async def boom(user_id: str, args: dict) -> str:
        raise RuntimeError("kaboom")

    perms = InMemoryPermissionStore(default_allowed={"boom"})
    registry = ToolRegistry(
        [Tool(ToolDefinition(name="boom", description="x", parameters={}), boom)], perms
    )
    result = await registry.execute("u", ToolCall(id="1", name="boom"))
    assert result.ok is False
    assert "kaboom" in result.observation
