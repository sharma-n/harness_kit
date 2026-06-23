"""Re-exports of the real persistence adapters.

Kept as a single import shim so any external caller that imported from here
continues to work. Imports are now deferred (PEP 562 module __getattr__) so
this module loads safely even when optional extras (redis, sqlite, qdrant)
are not installed.
"""

from __future__ import annotations

__all__ = [
    "RedisSessionStore",
    "SqliteProfileStore",
    "SqlitePermissionStore",
    "QdrantVectorStore",
]


def __getattr__(name: str):
    if name == "RedisSessionStore":
        from agent_kit.stores.redis_session import RedisSessionStore
        return RedisSessionStore
    if name == "SqliteProfileStore":
        from agent_kit.stores.sqlite_profile import SqliteProfileStore
        return SqliteProfileStore
    if name == "SqlitePermissionStore":
        from agent_kit.stores.sqlite_permissions import SqlitePermissionStore
        return SqlitePermissionStore
    if name == "QdrantVectorStore":
        from agent_kit.stores.qdrant_vectors import QdrantVectorStore
        return QdrantVectorStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
