"""Store Protocols (SPEC §4.2) — the swap seam for persistence backends.

Everything above ``stores/`` depends only on these Protocols, never on a
concrete adapter, so the in-memory reference implementations and the real
Redis / SQLite / Qdrant adapters are interchangeable. All methods are async: a
synchronous DB call on the event loop would stall every concurrent conversation.

Multi-user is enforced at this boundary:
  - ``SessionStore.load`` takes ``user_id`` and rejects cross-user access.
  - ``VectorStore.search`` always filters by ``user_id``.
  - ``PermissionStore`` resolves each user's allowed tool set.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_kit.stores.types import (
    MemoryHit,
    MemoryPoint,
    SessionState,
    Turn,
    UserProfile,
)


@runtime_checkable
class SessionStore(Protocol):
    """Hot working state, keyed by ``conversation_id`` and owned by a user."""

    async def load(self, conversation_id: str, user_id: str) -> SessionState | None:
        """Return the session, or ``None`` if absent.

        Raises ``UnauthorizedError`` if the session exists but is owned by a
        different user.
        """
        ...

    async def save(self, conversation_id: str, state: SessionState) -> None: ...

    async def append_turn(self, conversation_id: str, turn: Turn) -> None: ...


@runtime_checkable
class ProfileStore(Protocol):
    """Factual memory: per-user structured profile."""

    async def get(self, user_id: str) -> UserProfile: ...

    async def upsert_facts(self, user_id: str, facts: dict) -> None: ...


@runtime_checkable
class VectorStore(Protocol):
    """Episodic memory: per-user vector search."""

    async def add(self, points: list[MemoryPoint]) -> None: ...

    async def search(
        self,
        user_id: str,
        query_vector: list[float],
        k: int,
        min_score: float,
    ) -> list[MemoryHit]: ...


@runtime_checkable
class PermissionStore(Protocol):
    """Per-user tool allowlist (the multi-user authorization seam)."""

    async def allowed_tools(self, user_id: str) -> set[str]: ...

    async def grant(self, user_id: str, tools: set[str]) -> None: ...

    async def revoke(self, user_id: str, tools: set[str]) -> None: ...
