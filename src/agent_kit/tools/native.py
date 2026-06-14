"""Native in-repo tools (SPEC §8).

``remember_fact`` is the most reliable factual-write path ("remember that I
prefer X"); ``recall`` is an explicit episodic search for when the model wants to
dig beyond the auto-injected top-k. Automatic episodic retrieval still happens
during context assembly — these tools are additive.
"""

from __future__ import annotations

from typing import Any

from llm_kit import ToolDefinition

from agent_kit.memory.episodic import EpisodicMemory
from agent_kit.memory.factual import FactualMemory
from agent_kit.tools.base import Tool


def remember_fact_tool(factual: FactualMemory) -> Tool:
    async def handler(user_id: str, args: dict[str, Any]) -> str:
        key, value = str(args.get("key", "")), str(args.get("value", ""))
        if not key:
            return "error: 'key' is required"
        await factual.remember(user_id, key, value)
        return f"remembered: {key} = {value}"

    return Tool(
        definition=ToolDefinition(
            name="remember_fact",
            description="Persist a durable fact about the user for future conversations.",
            parameters={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Short fact name, e.g. 'seat_preference'."},
                    "value": {"type": "string", "description": "The fact's value."},
                },
                "required": ["key", "value"],
            },
        ),
        handler=handler,
    )


def recall_tool(episodic: EpisodicMemory) -> Tool:
    async def handler(user_id: str, args: dict[str, Any]) -> str:
        query = str(args.get("query", ""))
        if not query:
            return "error: 'query' is required"
        hits = await episodic.retrieve(user_id, query, recent_turns=[])
        if not hits:
            return "no relevant memories found"
        return "\n".join(
            f"- ({h.score:.2f}) {h.point.payload.get('text', '')}" for h in hits
        )

    return Tool(
        definition=ToolDefinition(
            name="recall",
            description="Search the user's past conversations for relevant memories.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for."},
                },
                "required": ["query"],
            },
        ),
        handler=handler,
    )
