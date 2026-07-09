"""Native in-repo tools (SPEC §8).

``remember_fact`` is the most reliable factual-write path ("remember that I
prefer X"); ``recall`` is an explicit episodic search for when the model wants to
dig beyond the auto-injected top-k. Automatic episodic retrieval still happens
during context assembly — these tools are additive.
"""

from __future__ import annotations

from typing import Any

from llm_kit import ToolDefinition

from harness_kit.memory.episodic import EpisodicMemory
from harness_kit.memory.factual import FactualMemory
from harness_kit.tools.base import Tool


def remember_fact_tool(factual: FactualMemory, *, episodic_enabled: bool = True) -> Tool:
    async def handler(user_id: str, args: dict[str, Any]) -> str:
        key, value = str(args.get("key", "")), str(args.get("value", ""))
        if not key:
            return "error: 'key' is required"
        await factual.remember(user_id, key, value)
        return f"remembered: {key} = {value}"

    episodic_note = (
        " Do not use for past discussion topics or conversation context"
        " — those belong in episodic memory."
        if episodic_enabled
        else " Do not use for transient discussion topics or one-off conversation context."
    )
    return Tool(
        definition=ToolDefinition(
            name="remember_fact",
            description=(
                "Store or update a durable fact about the user for future conversations. "
                "Use this for anything timeless and true about the user: preferences, "
                "occupation, habits, skills, location, dietary needs, constraints, or any "
                "other stable attribute. Calling with the same key overwrites the previous "
                "value, so this also serves as an update." + episodic_note
            ),
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


def forget_fact_tool(factual: FactualMemory) -> Tool:
    async def handler(user_id: str, args: dict[str, Any]) -> str:
        key = str(args.get("key", ""))
        if not key:
            return "error: 'key' is required"
        existed = await factual.forget(user_id, key)
        return f"forgot: {key}" if existed else f"no such fact: {key}"

    return Tool(
        definition=ToolDefinition(
            name="forget_fact",
            description=(
                "Delete a fact about the user by its key. Use this when the user explicitly "
                "asks to forget something, or when a fact is no longer true and should be "
                "removed entirely rather than updated."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "The fact name to remove."},
                },
                "required": ["key"],
            },
        ),
        handler=handler,
    )


def list_facts_tool(factual: FactualMemory) -> Tool:
    async def handler(user_id: str, args: dict[str, Any]) -> str:
        profile = await factual.get(user_id)
        if not profile.facts:
            return "no facts stored"
        return "\n".join(f"- {k}: {v}" for k, v in profile.facts.items())

    return Tool(
        definition=ToolDefinition(
            name="list_facts",
            description="List all durable facts currently remembered about the user.",
            parameters={"type": "object", "properties": {}},
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
        lines = []
        for h in hits:
            conv_id = h.point.payload.get("conversation_id", "")
            text = h.point.payload.get("text", "")
            lines.append(f"- [{conv_id}] ({h.score:.2f}) {text}")
        return "\n".join(lines)

    return Tool(
        definition=ToolDefinition(
            name="recall",
            description=(
                "Search the user's past conversation topics and discussion threads for relevant "
                "context. Use this to find past situations, problems the user worked through, or "
                "topics explored — not to look up facts about the user (use list_facts for that). "
                "Each result is prefixed with [conversation_id]; pass that ID to forget_memory "
                "to remove a specific memory."
            ),
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


def forget_memory_tool(episodic: EpisodicMemory) -> Tool:
    async def handler(user_id: str, args: dict[str, Any]) -> str:
        conversation_id = str(args.get("conversation_id", ""))
        if not conversation_id:
            return "error: 'conversation_id' is required"
        deleted = await episodic.forget_conversation(user_id, conversation_id)
        if deleted == 0:
            return f"no memory found for conversation_id: {conversation_id}"
        return f"forgot {deleted} memory point(s) for conversation: {conversation_id}"

    return Tool(
        definition=ToolDefinition(
            name="forget_memory",
            description=(
                "Delete all episodic memory (past conversation records) associated with a "
                "specific conversation. Use this when the user explicitly asks to forget a "
                "past conversation. Find the conversation_id from the recall tool's output "
                "(shown in brackets before each result). This action is irreversible — the "
                "conversation's embeddings are permanently removed."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "conversation_id": {
                        "type": "string",
                        "description": "The conversation_id to forget, as shown in recall output.",
                    },
                },
                "required": ["conversation_id"],
            },
        ),
        handler=handler,
    )
