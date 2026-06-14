"""AgentEvent → JSON wire frame (SPEC §10).

A stable ``type`` discriminator lets websocket and SSE share one encoder; the
client switches on it to render text, tool hints, and the terminal summary.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from agent_kit.agent.events import (
    AgentEvent,
    TextDelta,
    ToolCallStarted,
    ToolResult,
    TurnComplete,
)


def encode_event(event: AgentEvent) -> dict[str, Any]:
    if isinstance(event, TextDelta):
        return {"type": "text", "text": event.text}
    if isinstance(event, ToolCallStarted):
        return {
            "type": "tool_call",
            "call_id": event.call_id,
            "name": event.name,
            "arguments": event.arguments,
        }
    if isinstance(event, ToolResult):
        return {
            "type": "tool_result",
            "call_id": event.call_id,
            "name": event.name,
            "ok": event.ok,
            "content": event.content,
        }
    if isinstance(event, TurnComplete):
        return {
            "type": "turn_complete",
            "usage": asdict(event.usage),
            "iterations": event.iterations,
            "stop_reason": event.stop_reason,
        }
    raise TypeError(f"unknown event type: {type(event).__name__}")
