"""Live tests: tool call cycle.

Verifies that a real LLM calls a tool, receives the result, and produces a
final answer — a minimum of two LLM iterations per turn.
"""

from __future__ import annotations

import pytest

from agent_kit.agent.events import ToolCallStarted, ToolResult, TurnComplete

from tests.integration.conftest import requires_live, run_turn

pytestmark = requires_live

USER_ID = "live-test-user-tool-roundtrip"
_REMEMBER_MSG = "Please remember that my favorite color is blue."


async def test_remember_fact_tool_called(live_service, conv_id):
    events = await run_turn(live_service, USER_ID, conv_id, _REMEMBER_MSG)

    names = [e.name for e in events if isinstance(e, ToolCallStarted)]
    assert "remember_fact" in names, f"expected remember_fact in tool calls, got {names}"


async def test_tool_result_is_ok(live_service, conv_id):
    events = await run_turn(live_service, USER_ID, conv_id, _REMEMBER_MSG)

    results = [e for e in events if isinstance(e, ToolResult) and e.name == "remember_fact"]
    assert results, "expected at least one remember_fact ToolResult"
    assert all(r.ok for r in results), f"expected ok=True, got {results}"


async def test_tool_call_produces_min_two_iterations(live_service, conv_id):
    events = await run_turn(live_service, USER_ID, conv_id, _REMEMBER_MSG)

    turn_complete = next(e for e in events if isinstance(e, TurnComplete))
    assert turn_complete.iterations >= 2, (
        f"expected ≥2 iterations for a tool call, got {turn_complete.iterations}"
    )


async def test_fact_persists_in_store_after_tool_call(live_service, conv_id):
    await run_turn(live_service, USER_ID, conv_id, _REMEMBER_MSG)
    await live_service.agent.drain()

    profile = await live_service.stores.profile.get(USER_ID)
    assert profile.facts, "expected at least one fact stored after remember_fact call"
