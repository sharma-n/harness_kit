"""Tool registry (SPEC §8) — unifies native + (future) MCP tools, user-scoped.

The registry is the per-user authorization seam for tools:
  - ``definitions(user_id)`` returns only the tools that user is allowed to use,
    so the model is never even offered a tool outside the user's allowlist.
  - ``execute(user_id, call)`` re-checks permission before running (defense in
    depth), applies per-tool policy (rate limit + timeout, SPEC §8 / M10), and
    turns *every* failure — denied, rate-limited, unknown, raised, timed out — into
    a ``ToolResult(ok=False)`` observation rather than raising. Tool errors are
    observations, not exceptions (SPEC §5).

Per-tool policy (``ToolPolicy``, keyed by tool name) overrides the global timeout
and adds an optional per-user rate limit; unset fields fall back to the defaults.

The full observation text is fed back to the model; ``ToolResult.content`` shown
in the UI trace is truncated.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from llm_kit import ToolCall, ToolDefinition

from agent_kit.config import ToolPolicy
from agent_kit.stores.base import PermissionStore
from agent_kit.tools.base import Tool
from agent_kit.tools.ratelimit import ToolRateLimiter
from agent_kit import telemetry

_DISPLAY_TRUNCATE = 500


@dataclass(slots=True)
class Execution:
    """Result of running one tool call.

    ``display`` is truncated for the UI trace; ``observation`` is the full text
    fed back to the model. The agent loop maps this into a ``ToolResult`` event —
    keeping ``tools/`` below ``agent/`` in the layering.
    """

    call_id: str
    name: str
    ok: bool
    display: str
    observation: str


class ToolRegistry:
    def __init__(
        self,
        tools: list[Tool],
        permissions: PermissionStore,
        *,
        per_tool_timeout_s: float = 30.0,
        policies: dict[str, ToolPolicy] | None = None,
    ) -> None:
        self._tools = {t.name: t for t in tools}
        self._permissions = permissions
        self._timeout = per_tool_timeout_s
        self._policies = policies or {}
        self._ratelimiter = ToolRateLimiter()

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    async def definitions(self, user_id: str) -> list[ToolDefinition]:
        """Only the tools this user is allowed to use, in registration order."""
        allowed = await self._permissions.allowed_tools(user_id)
        return [t.definition for name, t in self._tools.items() if name in allowed]

    async def execute(self, user_id: str, call: ToolCall) -> Execution:
        with telemetry.span(
            f"tool.execute:{call.name}",
            kind="tool",
            input=call.arguments,
            call_id=call.id,
        ) as sp:
            execution, outcome = await self._run(user_id, call)
            sp.set_attributes(ok=execution.ok, outcome=outcome)
            sp.set_output(execution.observation)
            return execution

    async def _run(self, user_id: str, call: ToolCall) -> tuple[Execution, str]:
        """Run the tool call and report an outcome tag for the span (the loop only
        cares about the ``Execution``; the tag distinguishes the failure modes)."""
        allowed = await self._permissions.allowed_tools(user_id)
        if call.name not in allowed:
            return (
                self._failure(call, f"tool {call.name!r} is not permitted for this user"),
                "not_permitted",
            )

        tool = self._tools.get(call.name)
        if tool is None:
            return self._failure(call, f"unknown tool {call.name!r}"), "unknown_tool"

        policy = self._policies.get(call.name)
        if policy is not None and policy.rate_limit_per_minute is not None:
            if not self._ratelimiter.try_acquire(
                user_id, call.name, policy.rate_limit_per_minute
            ):
                return (
                    self._failure(
                        call,
                        f"tool {call.name!r} rate limit exceeded "
                        f"({policy.rate_limit_per_minute}/min for this user)",
                    ),
                    "rate_limited",
                )

        timeout = policy.timeout_s if policy and policy.timeout_s else self._timeout
        try:
            async with asyncio.timeout(timeout):
                content = await tool.handler(user_id, call.arguments)
        except asyncio.TimeoutError:
            return (
                self._failure(call, f"tool {call.name!r} timed out after {timeout}s"),
                "timed_out",
            )
        except Exception as exc:  # tool errors are observations, never crashes
            return self._failure(call, f"tool {call.name!r} failed: {exc}"), "error"

        return self._success(call, content), "ok"

    def _success(self, call: ToolCall, content: str) -> Execution:
        return Execution(
            call_id=call.id,
            name=call.name,
            ok=True,
            display=_truncate(content),
            observation=content,
        )

    def _failure(self, call: ToolCall, message: str) -> Execution:
        return Execution(
            call_id=call.id,
            name=call.name,
            ok=False,
            display=_truncate(message),
            observation=message,
        )


def _truncate(text: str, limit: int = _DISPLAY_TRUNCATE) -> str:
    return text if len(text) <= limit else text[:limit] + "… [truncated]"
