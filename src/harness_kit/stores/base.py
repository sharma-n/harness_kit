"""Store Protocols (SPEC §4.2) — the swap seam for persistence backends.

Everything above ``stores/`` depends only on these Protocols, never on a
concrete adapter, so the in-memory reference implementations and the real
Redis / SQLite / Qdrant adapters are interchangeable. All methods are async: a
synchronous DB call on the event loop would stall every concurrent conversation.

Multi-user is enforced at this boundary:
  - ``SessionStore.load`` takes ``user_id`` and rejects cross-user access.
  - ``VectorStore.search`` always filters by ``user_id``.
  - ``PermissionStore`` resolves each user's allowed tool set.
  - ``SkillStore`` resolves each user's visible skill set.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from harness_kit.stores.types import (
    ConversationMeta,
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

    async def due_for_finalize(self, idle_s: float) -> list[tuple[str, str]]:
        """Return ``(conversation_id, user_id)`` for sessions idle ≥ ``idle_s``
        that have not yet been finalized — the idle sweeper's work queue.
        """
        ...

    async def mark_finalized(self, conversation_id: str) -> None:
        """Record that a conversation has been finalized (episodic point written),
        so it is not re-finalized until new activity clears the mark.
        """
        ...

    async def list(self, user_id: str) -> list[ConversationMeta]:
        """All conversations owned by ``user_id``, metadata only (no transcripts),
        newest-first. User-scoped: never returns another user's conversations.
        """
        ...


@runtime_checkable
class ProfileStore(Protocol):
    """Factual memory: per-user structured profile."""

    async def get(self, user_id: str) -> UserProfile: ...

    async def upsert_facts(self, user_id: str, facts: dict) -> None: ...

    async def forget_facts(self, user_id: str, keys: set[str]) -> None:
        """Delete the named facts. A no-op for keys that are absent."""
        ...


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

    async def delete(self, point_ids: list[str], *, user_id: str) -> None:
        """Delete points by their harness_kit string IDs, verifying ownership.

        Each adapter must check ``payload["user_id"] == user_id`` before
        deleting. IDs that are missing or belong to a different user are
        silently skipped — never raise on a missing/mismatched ID.
        """
        ...

    async def list_points(
        self,
        user_id: str,
        kind: str | None = None,
        cursor: str | None = None,
        limit: int = 256,
    ) -> tuple[list[MemoryPoint], str | None]:
        """Paginated enumeration of a user's points, optionally filtered by
        ``kind`` ("conversation" | "moment").  User-scoped: only returns points
        owned by ``user_id``.  Returns (points, next_cursor) where ``next_cursor``
        is None when the last page is reached. Ordering is stable and suitable for
        full traversal but not necessarily sorted.
        """
        ...


@runtime_checkable
class PermissionStore(Protocol):
    """Per-user tool allowlist (the multi-user authorization seam).

    Allowed tools are computed as:
        allowed(user_id) = (default ∪ granted_delta) − revoked_delta

    This ensures that users automatically see future additions to the global default
    set, unless they explicitly revoke a tool. An explicit revoke is a stronger
    signal than a later default addition, so revoked tools stay revoked even if
    they're added to the default later.
    """

    async def allowed_tools(self, user_id: str) -> set[str]: ...

    async def grant(self, user_id: str, tools: set[str]) -> None: ...

    async def revoke(self, user_id: str, tools: set[str]) -> None: ...

    async def extend_default_allowed(self, names: set[str]) -> None:
        """Fold ``names`` into the global default allowlist (the fallback for users
        with no explicit grant). Used at startup for ``auto_allow`` MCP servers.
        Automatically flows to all users except those who explicitly revoke.
        """
        ...


@runtime_checkable
class SkillStore(Protocol):
    """Per-user skill visibility grants.

    ``allowed_skills()`` returns ``None`` to mean "all skills allowed" — the v1
    default where every user can see every installed skill. A ``set[str]`` means
    the user is restricted to exactly those skill names.

    The skill *content* lives on disk as ``SKILL.md`` files; this Protocol only
    stores grant state (which users may access which skills).
    """

    async def allowed_skills(self, user_id: str) -> set[str] | None:
        """Return the user's allowed skill names, or ``None`` for unrestricted."""
        ...

    async def grant(self, user_id: str, skills: set[str]) -> None:
        """Add skills to a user's explicit allowlist."""
        ...

    async def revoke(self, user_id: str, skills: set[str]) -> None:
        """Remove skills from a user's explicit allowlist."""
        ...

    async def extend_default_allowed(self, names: set[str]) -> None:
        """Grow the global default allowlist (for future auto-grant installs)."""
        ...
