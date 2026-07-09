"""The agent event stream (SPEC §4.1) — the load-bearing abstraction.

A streaming multi-step tool loop cannot yield bare tokens: mid-response the model
may emit a tool call, forcing the loop to pause, execute, and resume. So
``run_turn`` yields these typed events and ``serving/`` translates them to wire
frames.

``TokenUsage`` is reused from llm_kit verbatim (SPEC §13).
"""

from __future__ import annotations

from dataclasses import dataclass

from llm_kit.llm.response import TokenUsage

__all__ = [
    "AgentEvent",
    "TextDelta",
    "ToolCallStarted",
    "ToolApprovalRequired",
    "ToolResult",
    "TurnComplete",
    "TurnFailed",
]


@dataclass(slots=True)
class TextDelta:
    """A text delta — forward to the user immediately (time-to-first-token)."""

    text: str


@dataclass(slots=True)
class ToolCallStarted:
    """UI hint: "calling search_web(...)".

    Unlike llm_kit's mid-stream hint, ``arguments`` is populated — this is emitted
    at execution time from the fully-assembled ``StreamEnd.response.tool_calls``.
    """

    call_id: str
    name: str
    arguments: dict


@dataclass(slots=True)
class ToolApprovalRequired:
    """Pause: the agent needs human approval before executing this tool.

    Over WebSocket: the client responds on the same connection with
    ``{"type": "approval", "call_id": ..., "approved": true|false}``.
    Over SSE: automatically denied (SSE is one-way).

    If no response arrives within ``timeout_s`` the loop auto-denies.
    """

    call_id: str
    name: str
    arguments: dict
    timeout_s: float


@dataclass(slots=True)
class ToolResult:
    """Optional UI trace of an observation.

    ``content`` is truncated for display; the full text is fed back to the model.
    """

    call_id: str
    name: str
    ok: bool
    content: str


@dataclass(slots=True)
class TurnComplete:
    """Terminal event: usage, stop reason, and tool-loop iteration count."""

    usage: TokenUsage
    iterations: int
    stop_reason: str


@dataclass(slots=True)
class TurnFailed:
    """Terminal event: a turn failed with an error before completing normally.

    Carries a human-readable error message for the client. Whatever partial state
    was completable before the failure (e.g., assistant text, tool calls) has already
    been persisted.
    """

    error: str


AgentEvent = TextDelta | ToolCallStarted | ToolApprovalRequired | ToolResult | TurnComplete | TurnFailed
