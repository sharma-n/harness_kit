"""Working memory (SPEC §7.1): recent-turn buffer + rolling summary over Redis.

Read is hot (every turn); append is synchronous (microseconds). When the buffer
exceeds ``buffer_turns`` the oldest turns are eligible for rollover into the
rolling summary — the summarizer call is wired but, this pass, the actual
re-summarization runs off the hot path (enqueued; see ``WorkingMemory.rollover``).
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_kit.config import WorkingMemoryConfig
from agent_kit.stores.base import SessionStore
from agent_kit.stores.types import SessionState, Turn


@dataclass(slots=True)
class WorkingSnapshot:
    """What the context builder needs from working memory for one turn."""

    buffer: list[Turn]
    summary: str


class WorkingMemory:
    def __init__(self, session_store: SessionStore, cfg: WorkingMemoryConfig) -> None:
        self._store = session_store
        self._cfg = cfg

    async def load(self, conversation_id: str, user_id: str) -> WorkingSnapshot:
        """Read the buffer + rolling summary for a conversation.

        Creates and persists an empty, user-owned session on first contact so the
        session is owned from turn one (enforces ownership on every later load).
        """
        state = await self._store.load(conversation_id, user_id)
        if state is None:
            state = SessionState(user_id=user_id)
            await self._store.save(conversation_id, state)
        return WorkingSnapshot(
            buffer=list(state.working_buffer), summary=state.rolling_summary
        )

    async def append_turn(self, conversation_id: str, turn: Turn) -> None:
        await self._store.append_turn(conversation_id, turn)

    def needs_rollover(self, buffer: list[Turn]) -> bool:
        return len(buffer) > self._cfg.summary_trigger

    @property
    def buffer_turns(self) -> int:
        return self._cfg.buffer_turns
