"""Signature stubs for the real persistence adapters (next milestone).

Present so the Protocol contracts are exercised and the factory has registration
points; each raises ``NotImplementedError`` until implemented. Kept in one module
to avoid importing redis / sqlalchemy / qdrant-client (optional extras) before
they are needed.
"""

from __future__ import annotations

from agent_kit.stores.types import (
    ConversationMeta,
    MemoryHit,
    MemoryPoint,
    SessionState,
    Turn,
    UserProfile,
)

_MSG = "real {} adapter not implemented yet; use the in-memory backend"


class RedisSessionStore:
    """SPEC §9.1 — Redis hash per ``session:{conversation_id}`` with idle TTL."""

    def __init__(self, url: str, ttl_s: int | None = None) -> None:
        raise NotImplementedError(_MSG.format("redis session"))

    async def load(self, conversation_id: str, user_id: str) -> SessionState | None: ...
    async def save(self, conversation_id: str, state: SessionState) -> None: ...
    async def append_turn(self, conversation_id: str, turn: Turn) -> None: ...
    async def due_for_finalize(self, idle_s: float) -> list[tuple[str, str]]: ...
    async def mark_finalized(self, conversation_id: str) -> None: ...
    async def list(self, user_id: str) -> list[ConversationMeta]: ...


class SqliteProfileStore:
    """SPEC §9.2 — profiles table via SQLAlchemy + aiosqlite (→ Postgres)."""

    def __init__(self, url: str) -> None:
        raise NotImplementedError(_MSG.format("sqlite profile"))

    async def get(self, user_id: str) -> UserProfile: ...
    async def upsert_facts(self, user_id: str, facts: dict) -> None: ...
    async def forget_facts(self, user_id: str, keys: set[str]) -> None: ...


class QdrantVectorStore:
    """SPEC §9.3 — Qdrant collection, always filtered by ``user_id``."""

    def __init__(self, url: str, collection: str) -> None:
        raise NotImplementedError(_MSG.format("qdrant vector"))

    async def add(self, points: list[MemoryPoint]) -> None: ...
    async def search(
        self, user_id: str, query_vector: list[float], k: int, min_score: float
    ) -> list[MemoryHit]: ...


class SqlitePermissionStore:
    """Per-user tool allowlist persisted in SQLite, default-fallback aware."""

    def __init__(self, url: str, default_allowed: set[str]) -> None:
        raise NotImplementedError(_MSG.format("sqlite permission"))

    async def allowed_tools(self, user_id: str) -> set[str]: ...
    async def grant(self, user_id: str, tools: set[str]) -> None: ...
    async def revoke(self, user_id: str, tools: set[str]) -> None: ...
    async def extend_default_allowed(self, names: set[str]) -> None: ...
