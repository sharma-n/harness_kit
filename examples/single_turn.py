"""Drive one multi-turn conversation directly against the Agent (no server).

    OPENAI_API_KEY=... uv run python examples/single_turn.py

Streams TextDeltas to stdout and prints the per-turn usage summary. Uses the
in-memory stores from config.yaml, so no Redis/Qdrant/SQLite needed.
"""

from __future__ import annotations

import asyncio

from agent_kit.agent.events import TextDelta, ToolCallStarted, ToolResult, TurnComplete
from agent_kit.service import AgentService

USER_ID = "demo-user"
CONVERSATION_ID = "demo-conversation"


async def say(service: AgentService, message: str) -> None:
    print(f"\n>>> {message}\n")
    async for event in service.agent.run_turn(USER_ID, CONVERSATION_ID, message):
        if isinstance(event, TextDelta):
            print(event.text, end="", flush=True)
        elif isinstance(event, ToolCallStarted):
            print(f"\n[calling {event.name}({event.arguments})]")
        elif isinstance(event, ToolResult):
            print(f"\n[{event.name} -> ok={event.ok}: {event.content}]")
        elif isinstance(event, TurnComplete):
            print(
                f"\n--- turn done: {event.iterations} iter(s), "
                f"stop={event.stop_reason}, usage={event.usage}"
            )


async def main() -> None:
    service = AgentService.from_yaml("config.yaml")
    try:
        await say(service, "Hi! Remember that I prefer aisle seats.")
        await say(service, "What seat do I prefer again?")
    finally:
        await service.aclose()


if __name__ == "__main__":
    asyncio.run(main())
