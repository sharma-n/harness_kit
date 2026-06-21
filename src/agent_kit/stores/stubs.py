"""Re-exports of the real persistence adapters.

Kept as a single import shim so ``factory.py`` (and any other caller that
imported from here) continues to work without change after the adapters were
moved into their own modules.
"""

from agent_kit.stores.qdrant_vectors import QdrantVectorStore
from agent_kit.stores.redis_session import RedisSessionStore
from agent_kit.stores.sqlite_permissions import SqlitePermissionStore
from agent_kit.stores.sqlite_profile import SqliteProfileStore

__all__ = [
    "RedisSessionStore",
    "SqliteProfileStore",
    "SqlitePermissionStore",
    "QdrantVectorStore",
]
