"""Agent loop event sequences + safety rails (SPEC §5, §15)."""

from __future__ import annotations

from harness_kit.agent.events import (
    TextDelta,
    ToolApprovalRequired,
    ToolCallStarted,
    ToolResult,
    TurnComplete,
)
from harness_kit.config import AgentConfig, HarnessKitConfig, ToolPolicy
from harness_kit.tools.base import Tool
from llm_kit import ToolDefinition

from tests.conftest import ScriptedTurn, make_service, tc


def _flight_tool(calls: list[str]) -> Tool:
    async def handler(user_id: str, args: dict) -> str:
        calls.append(args.get("city", "?"))
        return f"booked flight to {args.get('city')}"

    return Tool(
        ToolDefinition(name="book_flight", description="book a flight", parameters={}),
        handler,
    )


async def _collect(agent, user_id="alice", convo="c1", msg="hi"):
    events = [e async for e in agent.run_turn(user_id, convo, msg)]
    await agent.drain()  # let background memory writes finish before loop teardown
    return events


async def test_plain_answer_streams_text_then_completes(base_config):
    service, _ = make_service(
        base_config, turns=[ScriptedTurn(text_chunks=["Hello", " there"])]
    )
    events = await _collect(service.agent)
    assert [type(e).__name__ for e in events] == ["TextDelta", "TextDelta", "TurnComplete"]
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Hello there"
    done = events[-1]
    assert isinstance(done, TurnComplete)
    assert done.iterations == 1
    assert done.stop_reason == "completed"


async def test_tool_call_then_final_answer(base_config):
    calls: list[str] = []
    # Grant the tool to alice via default allowlist.
    base_config.tools.default_allowed = ["book_flight"]
    service, _ = make_service(
        base_config,
        turns=[
            ScriptedTurn(tool_calls=[tc("call-1", "book_flight", city="NYC")]),
            ScriptedTurn(text_chunks=["Done, you're booked."]),
        ],
        extra_tools=[_flight_tool(calls)],
    )
    events = await _collect(service.agent)
    kinds = [type(e).__name__ for e in events]
    assert kinds == [
        "ToolCallStarted",
        "ToolResult",
        "TextDelta",
        "TurnComplete",
    ]
    started = events[0]
    assert isinstance(started, ToolCallStarted)
    assert started.arguments == {"city": "NYC"}
    result = events[1]
    assert isinstance(result, ToolResult)
    assert result.ok is True
    assert calls == ["NYC"]
    assert events[-1].iterations == 2


async def test_max_iterations_cap_stops_gracefully(base_config):
    base_config.agent = AgentConfig(max_iterations=3, system_prompt="x")
    base_config.tools.default_allowed = ["book_flight"]
    # Model always wants to call a tool → would loop forever without the cap.
    looping = [
        ScriptedTurn(tool_calls=[tc(f"c{i}", "book_flight", city="X")]) for i in range(10)
    ]
    service, _ = make_service(base_config, turns=looping, extra_tools=[_flight_tool([])])
    events = await _collect(service.agent)
    done = events[-1]
    assert isinstance(done, TurnComplete)
    assert done.iterations == 3
    assert done.stop_reason == "max_iterations"


async def test_failed_tool_is_observation_and_loop_continues(base_config):
    base_config.tools.default_allowed = ["book_flight"]

    async def boom(user_id: str, args: dict) -> str:
        raise RuntimeError("provider down")

    bad_tool = Tool(
        ToolDefinition(name="book_flight", description="x", parameters={}), boom
    )
    service, fake = make_service(
        base_config,
        turns=[
            ScriptedTurn(tool_calls=[tc("c1", "book_flight", city="NYC")]),
            ScriptedTurn(text_chunks=["Sorry, booking failed."]),
        ],
        extra_tools=[bad_tool],
    )
    events = await _collect(service.agent)
    result = next(e for e in events if isinstance(e, ToolResult))
    assert result.ok is False
    assert "provider down" in result.content
    # The error observation was fed back: a 2nd stream call happened.
    assert len(fake.stream_calls) == 2
    assert events[-1].stop_reason == "completed"


async def test_working_memory_persists_across_turns(base_config):
    service, _ = make_service(
        base_config,
        turns=[
            ScriptedTurn(text_chunks=["Hi Sam."]),
            ScriptedTurn(text_chunks=["You said hello earlier."]),
        ],
    )
    await _collect(service.agent, msg="hello, I'm Sam")
    # Second turn's assembled context should include the first turn's buffer.
    await _collect(service.agent, msg="what did I say?")
    state = await service.stores.session.load("c1", "alice")
    texts = [t.text for t in state.working_buffer]
    assert "hello, I'm Sam" in texts
    assert "Hi Sam." in texts


# ---------------------------------------------------------------------------
# HITL approval tests
# ---------------------------------------------------------------------------

def _email_tool(calls: list[str]) -> Tool:
    async def handler(user_id: str, args: dict) -> str:
        calls.append(args.get("to", "?"))
        return f"email sent to {args.get('to')}"

    return Tool(
        ToolDefinition(name="send_email", description="send an email", parameters={}),
        handler,
    )


async def _collect_with_approval(
    agent, *, approve: dict[str, bool], user_id="alice", convo="c1", msg="hi"
):
    """Collect events, resolving any ToolApprovalRequired inline.

    The future is resolved before __anext__ resumes the loop, so wait_for
    returns immediately — no real-time wait.
    """
    events = []
    async for event in agent.run_turn(user_id, convo, msg):
        events.append(event)
        if isinstance(event, ToolApprovalRequired):
            agent.resolve_approval(event.call_id, approve.get(event.call_id, False), conversation_id=convo)
    await agent.drain()
    return events


async def test_approval_required_approved(base_config):
    calls: list[str] = []
    base_config.tools.default_allowed = ["send_email"]
    base_config.tools.definitions = {
        "send_email": ToolPolicy(requires_approval=True, approval_timeout_s=5.0)
    }
    service, _ = make_service(
        base_config,
        turns=[
            ScriptedTurn(tool_calls=[tc("call-1", "send_email", to="bob@example.com")]),
            ScriptedTurn(text_chunks=["Email sent!"]),
        ],
        extra_tools=[_email_tool(calls)],
    )
    events = await _collect_with_approval(service.agent, approve={"call-1": True})
    kinds = [type(e).__name__ for e in events]
    assert kinds == [
        "ToolApprovalRequired",
        "ToolCallStarted",
        "ToolResult",
        "TextDelta",
        "TurnComplete",
    ]
    approval = events[0]
    assert isinstance(approval, ToolApprovalRequired)
    assert approval.name == "send_email"
    assert approval.arguments == {"to": "bob@example.com"}
    assert approval.timeout_s == 5.0
    assert events[2].ok is True
    assert calls == ["bob@example.com"]  # tool actually ran


async def test_approval_required_denied(base_config):
    calls: list[str] = []
    base_config.tools.default_allowed = ["send_email"]
    base_config.tools.definitions = {
        "send_email": ToolPolicy(requires_approval=True, approval_timeout_s=5.0)
    }
    service, fake = make_service(
        base_config,
        turns=[
            ScriptedTurn(tool_calls=[tc("call-1", "send_email", to="bob@example.com")]),
            ScriptedTurn(text_chunks=["Okay, won't send it."]),
        ],
        extra_tools=[_email_tool(calls)],
    )
    events = await _collect_with_approval(service.agent, approve={"call-1": False})
    kinds = [type(e).__name__ for e in events]
    assert kinds == [
        "ToolApprovalRequired",
        "ToolResult",
        "TextDelta",
        "TurnComplete",
    ]
    result = events[1]
    assert isinstance(result, ToolResult)
    assert result.ok is False
    assert "user denied approval" in result.content
    assert calls == []  # handler never ran
    assert len(fake.stream_calls) == 2  # denial fed back as observation → 2nd LLM call


async def test_approval_timeout(base_config):
    calls: list[str] = []
    base_config.tools.default_allowed = ["send_email"]
    base_config.tools.definitions = {
        "send_email": ToolPolicy(requires_approval=True, approval_timeout_s=0.01)
    }
    service, _ = make_service(
        base_config,
        turns=[
            ScriptedTurn(tool_calls=[tc("call-1", "send_email", to="bob@example.com")]),
            ScriptedTurn(text_chunks=["Approval timed out."]),
        ],
        extra_tools=[_email_tool(calls)],
    )
    # Plain _collect: nobody resolves the approval → wait_for times out after 0.01s
    events = await _collect(service.agent)
    result = next(e for e in events if isinstance(e, ToolResult))
    assert result.ok is False
    assert "timed out" in result.content
    assert calls == []


async def test_non_approval_tool_unaffected(base_config):
    """A tool without requires_approval still emits ToolCallStarted with no pause."""
    calls: list[str] = []
    base_config.tools.default_allowed = ["send_email"]
    # No definitions entry → get_policy returns None → requires_approval defaults False
    service, _ = make_service(
        base_config,
        turns=[
            ScriptedTurn(tool_calls=[tc("call-1", "send_email", to="bob@example.com")]),
            ScriptedTurn(text_chunks=["Done."]),
        ],
        extra_tools=[_email_tool(calls)],
    )
    events = await _collect(service.agent)
    kinds = [type(e).__name__ for e in events]
    assert kinds == ["ToolCallStarted", "ToolResult", "TextDelta", "TurnComplete"]
    assert calls == ["bob@example.com"]
