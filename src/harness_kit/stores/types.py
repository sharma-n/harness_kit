"""Data records the stores persist.

Leaf module: depends only on llm_kit's leaf tool types, so every layer above can
import these without cycles.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from llm_kit import ToolCall


@dataclass(slots=True)
class Turn:
    """One conversation turn as stored in working memory.

    A turn is a single role-tagged utterance (not a user+assistant pair), so the
    working buffer is a flat list replayable directly as ``Message``s.
    """

    role: str  # "user" | "assistant" | "tool"
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    ts: float = field(default_factory=time.time)


@dataclass(slots=True)
class SessionState:
    """Hot working state for one conversation, owned by ``user_id``."""

    user_id: str
    working_buffer: list[Turn] = field(default_factory=list)
    rolling_summary: str = ""
    model_name: str | None = None  # per-conversation LLM override; None = service default
    scratch: dict[str, Any] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)
    # Set when the conversation has been embedded as an episodic point at idle.
    # Cleared on any new activity (save/append_turn) so a resumed conversation is
    # re-finalized later. Lets the idle sweeper finalize each conversation once per
    # idle cycle without re-embedding it on every pass.
    finalized_at: float | None = None
    # When the conversation was first created. Stable across resumes (set once at
    # construction); surfaced by ``SessionStore.list`` for conversation listing (M11).
    created_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class ConversationMeta:
    """Lightweight conversation metadata for listing (M11) — no transcript.

    Built by ``SessionStore.list`` so a chat UI can render history without pulling
    full buffers. ``summary_preview`` is a truncated slice of the rolling summary.
    The contract is intentionally transcript-free and stable: a future durable
    transcript store can populate the same fields, and ``ConversationDetail`` (turns)
    composes this rather than redefining it.
    """

    conversation_id: str
    user_id: str
    created_at: float
    updated_at: float
    finalized_at: float | None
    turn_count: int
    summary_preview: str
    model_name: str | None = None


@dataclass(slots=True)
class UserProfile:
    """Factual memory: a per-user structured profile."""

    user_id: str
    facts: dict[str, Any] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class MemoryPoint:
    """One episodic-memory point (a turn or pair) with its embedding + payload.

    ``payload`` always includes ``user_id`` so the vector store can enforce
    per-user isolation on search.
    """

    id: str
    vector: list[float]
    payload: dict[str, Any]


@dataclass(slots=True)
class MemoryHit:
    """A retrieved episodic point with its similarity score."""

    point: MemoryPoint
    score: float


