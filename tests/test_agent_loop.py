"""Agent loop event sequences + safety rails (SPEC §5, §15)."""

from __future__ import annotations

from agent_kit.agent.events import TextDelta, ToolCallStarted, ToolResult, TurnComplete
from agent_kit.config import AgentConfig, AgentKitConfig
from agent_kit.tools.base import Tool
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
