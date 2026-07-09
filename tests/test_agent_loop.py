"""Agent loop event sequences + safety rails (SPEC §5, §15)."""

from __future__ import annotations

import asyncio

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


# ---------------------------------------------------------------------------
# Parallel tool-call tests
# ---------------------------------------------------------------------------


def _controllable_tool(name: str, release: asyncio.Event, started: list[str]) -> Tool:
    """A tool that waits on a release event before returning."""
    async def handler(user_id: str, args: dict) -> str:
        started.append(name)
        await release.wait()
        return f"{name} done"

    return Tool(
        ToolDefinition(name=name, description="x", parameters={}),
        handler,
    )


def _instant_tool(name: str, started: list[str]) -> Tool:
    """A tool that returns immediately."""
    async def handler(user_id: str, args: dict) -> str:
        started.append(name)
        return f"{name} done"

    return Tool(
        ToolDefinition(name=name, description="x", parameters={}),
        handler,
    )


async def test_parallel_calls_execute_concurrently_order_preserved(base_config):
    """Two non-approval calls both execute concurrently; message order matches call order."""
    base_config.tools.default_allowed = ["slow_tool", "fast_tool"]
    release = asyncio.Event()
    started: list[str] = []

    service, fake = make_service(
        base_config,
        turns=[
            ScriptedTurn(
                tool_calls=[
                    tc("call-1", "slow_tool"),
                    tc("call-2", "fast_tool"),
                ]
            ),
            ScriptedTurn(text_chunks=["Done."]),
        ],
        extra_tools=[
            _controllable_tool("slow_tool", release, started),
            _instant_tool("fast_tool", started),
        ],
    )

    # Drive the generator manually so we can check intermediate state.
    gen = service.agent.run_turn("alice", "c1", "hi")
    events = []
    fast_tool_result_index = None

    # Collect events until fast_tool's ToolResult appears, without releasing slow_tool.
    try:
        while True:
            event = await gen.__anext__()
            events.append(event)
            if isinstance(event, ToolResult) and event.call_id == "call-2":
                fast_tool_result_index = len(events) - 1
                break
    except StopAsyncIteration:
        pass

    # Verify fast_tool completed while slow_tool was still blocked.
    assert fast_tool_result_index is not None, "fast_tool should have completed before release"
    assert started == ["slow_tool", "fast_tool"], "both tools should have started"

    # Now release slow_tool and drain the rest.
    release.set()
    try:
        while True:
            events.append(await gen.__anext__())
    except StopAsyncIteration:
        pass
    await service.agent.drain()

    # Verify the LLM saw both tool results in the original call order.
    # fake.stream_calls[1] is the second LLM invocation (after tool results are fed back).
    assert len(fake.stream_calls) == 2
    second_invocation_messages = fake.stream_calls[1]
    # Find the two tool-result entries in messages (TOOL role with tool_call_id set).
    tool_results = [m for m in second_invocation_messages if m.role == "tool" and m.tool_call_id]
    assert len(tool_results) == 2
    assert tool_results[0].tool_call_id == "call-1", "call-1 result should come first"
    assert tool_results[1].tool_call_id == "call-2", "call-2 result should come second"


async def test_parallel_approvals_both_pending_before_either_resolved(base_config):
    """Two approval-required calls in one batch both emit ToolApprovalRequired before either is resolved."""
    base_config.tools.default_allowed = ["send_email_a", "send_email_b"]
    base_config.tools.definitions = {
        "send_email_a": ToolPolicy(requires_approval=True, approval_timeout_s=5.0),
        "send_email_b": ToolPolicy(requires_approval=True, approval_timeout_s=5.0),
    }

    calls: list[str] = []

    def _email_tool_a(calls: list[str]) -> Tool:
        async def handler(user_id: str, args: dict) -> str:
            calls.append("a")
            return "email a sent"
        return Tool(
            ToolDefinition(name="send_email_a", description="send email a", parameters={}),
            handler,
        )

    def _email_tool_b(calls: list[str]) -> Tool:
        async def handler(user_id: str, args: dict) -> str:
            calls.append("b")
            return "email b sent"
        return Tool(
            ToolDefinition(name="send_email_b", description="send email b", parameters={}),
            handler,
        )

    service, _ = make_service(
        base_config,
        turns=[
            ScriptedTurn(
                tool_calls=[
                    tc("call-1", "send_email_a"),
                    tc("call-2", "send_email_b"),
                ]
            ),
            ScriptedTurn(text_chunks=["Both sent."]),
        ],
        extra_tools=[_email_tool_a(calls), _email_tool_b(calls)],
    )

    # Drive manually and collect events until both ToolApprovalRequired are seen.
    gen = service.agent.run_turn("alice", "c1", "hi")
    events = []
    approval_call_ids = set()

    try:
        while len(approval_call_ids) < 2:
            event = await gen.__anext__()
            events.append(event)
            if isinstance(event, ToolApprovalRequired):
                approval_call_ids.add(event.call_id)
    except StopAsyncIteration:
        pass

    # Before either approval was resolved, no ToolCallStarted or ToolResult should have appeared.
    for event in events:
        assert not isinstance(event, ToolCallStarted), "ToolCallStarted should not appear before approvals"
        assert not isinstance(event, ToolResult), "ToolResult should not appear before approvals"

    assert approval_call_ids == {"call-1", "call-2"}, "both approvals should have been emitted"

    # Now resolve both approvals.
    service.agent.resolve_approval("call-1", True, conversation_id="c1")
    service.agent.resolve_approval("call-2", True, conversation_id="c1")

    # Drain the rest.
    try:
        while True:
            events.append(await gen.__anext__())
    except StopAsyncIteration:
        pass
    await service.agent.drain()

    # Both tools should have run.
    assert sorted(calls) == ["a", "b"]


async def test_mixed_approval_and_non_approval_batch(base_config):
    """One approval-required + one non-approval call in same batch run concurrently."""
    base_config.tools.default_allowed = ["send_email", "book_flight"]
    base_config.tools.definitions = {
        "send_email": ToolPolicy(requires_approval=True, approval_timeout_s=5.0),
    }

    calls: list[str] = []

    service, fake = make_service(
        base_config,
        turns=[
            ScriptedTurn(
                tool_calls=[
                    tc("call-1", "send_email", to="bob@example.com"),
                    tc("call-2", "book_flight", city="NYC"),
                ]
            ),
            ScriptedTurn(text_chunks=["All done."]),
        ],
        extra_tools=[_email_tool(calls), _flight_tool(calls)],
    )

    # Drive manually and collect until ToolApprovalRequired for call-1, then until
    # we see ToolCallStarted for call-2 (without resolving call-1's approval yet).
    gen = service.agent.run_turn("alice", "c1", "hi")
    events = []
    approval_seen = False
    call_2_started = False

    try:
        while True:
            event = await gen.__anext__()
            events.append(event)
            if isinstance(event, ToolApprovalRequired) and event.call_id == "call-1":
                approval_seen = True
            if approval_seen and isinstance(event, ToolCallStarted) and event.call_id == "call-2":
                call_2_started = True
                break
    except StopAsyncIteration:
        pass

    # Verify we saw the approval and call-2 started without call-1 being resolved.
    assert approval_seen, "should have seen approval for call-1"
    assert call_2_started, "should have seen ToolCallStarted for call-2 before call-1 was approved"

    # Now resolve call-1 and drain.
    service.agent.resolve_approval("call-1", True, conversation_id="c1")
    try:
        while True:
            events.append(await gen.__anext__())
    except StopAsyncIteration:
        pass
    await service.agent.drain()

    # Verify both tools ran and message order matches [call-1, call-2].
    assert sorted(calls) == ["NYC", "bob@example.com"]
    second_invocation_messages = fake.stream_calls[1]
    tool_results = [m for m in second_invocation_messages if m.role == "tool" and m.tool_call_id]
    assert len(tool_results) == 2
    assert tool_results[0].tool_call_id == "call-1", "call-1 result should come first"
    assert tool_results[1].tool_call_id == "call-2", "call-2 result should come second"
