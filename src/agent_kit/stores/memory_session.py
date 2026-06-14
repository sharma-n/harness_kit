"""In-memory SessionStore — the default working-state backend.

Mirrors the Redis adapter's contract (ownership check + idle TTL) so the real
adapter drops in behind the same Protocol with no change above ``stores/``.
"""

from __future__ import annotations

import time

from agent_kit.errors import UnauthorizedError
from agent_kit.stores.types import SessionState, Turn


class InMemorySessionStore:
    """Process-local sessions keyed by ``conversation_id``.

    Per SPEC §12 this is the one place per-user state is allowed to live in
    process memory — and only because it *is* the store. Real deployments swap
    in Redis behind the same Protocol.
    """

    def __init__(self, ttl_s: int | None = None) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._ttl_s = ttl_s

    async def load(self, conversation_id: str, user_id: str) -> SessionState | None:
        state = self._sessions.get(conversation_id)
        if state is None:
            return None
        if self._ttl_s is not None and time.time() - state.updated_at > self._ttl_s:
            del self._sessions[conversation_id]
            return None
        if state.user_id != user_id:
            raise UnauthorizedError(
                f"conversation {conversation_id!r} is not owned by user {user_id!r}"
            )
        return state

    async def save(self, conversation_id: str, state: SessionState) -> None:
        state.updated_at = time.time()
        state.finalized_at = None  # new activity → needs re-finalize when idle again
        self._sessions[conversation_id] = state

    async def append_turn(self, conversation_id: str, turn: Turn) -> None:
        state = self._sessions.get(conversation_id)
        if state is None:
            raise KeyError(f"no session {conversation_id!r}; call save() first")
        state.working_buffer.append(turn)
        state.updated_at = time.time()
        state.finalized_at = None  # new activity → needs re-finalize when idle again

    async def due_for_finalize(self, idle_s: float) -> list[tuple[str, str]]:
        now = time.time()
        return [
            (conversation_id, state.user_id)
            for conversation_id, state in self._sessions.items()
            if state.finalized_at is None and now - state.updated_at >= idle_s
        ]

    async def mark_finalized(self, conversation_id: str) -> None:
        state = self._sessions.get(conversation_id)
        if state is not None:
            state.finalized_at = time.time()
