"""Store assembly — the single place backends are selected from config.

``build_stores(cfg)`` returns a ``Stores`` bundle of the four Protocol-typed
adapters. Defaults to in-memory; flipping a ``*_backend`` in config swaps in a
real adapter with no change to any layer above ``stores/``.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_kit.config import AgentKitConfig, StoreBackend
from agent_kit.stores.base import (
    PermissionStore,
    ProfileStore,
    SessionStore,
    VectorStore,
)
from agent_kit.stores.memory_permissions import InMemoryPermissionStore
from agent_kit.stores.memory_profile import InMemoryProfileStore
from agent_kit.stores.memory_session import InMemorySessionStore
from agent_kit.stores.memory_vectors import InMemoryVectorStore


@dataclass(slots=True)
class Stores:
    session: SessionStore
    profile: ProfileStore
    vectors: VectorStore
    permissions: PermissionStore


def build_stores(cfg: AgentKitConfig) -> Stores:
    return Stores(
        session=_build_session(cfg),
        profile=_build_profile(cfg),
        vectors=_build_vectors(cfg),
        permissions=_build_permissions(cfg),
    )


def _build_session(cfg: AgentKitConfig) -> SessionStore:
    backend = cfg.stores.session_backend
    if backend is StoreBackend.MEMORY:
        return InMemorySessionStore(ttl_s=cfg.memory.working.ttl_s)
    if backend is StoreBackend.REDIS:
        from agent_kit.stores.redis_session import RedisSessionStore
        return RedisSessionStore(cfg.stores.redis.url, ttl_s=cfg.memory.working.ttl_s)
    raise ValueError(f"unsupported session backend: {backend}")


def _build_profile(cfg: AgentKitConfig) -> ProfileStore:
    backend = cfg.stores.profile_backend
    if backend is StoreBackend.MEMORY:
        return InMemoryProfileStore()
    if backend is StoreBackend.SQLITE:
        from agent_kit.stores.sqlite_profile import SqliteProfileStore
        return SqliteProfileStore(cfg.stores.sqlite.url)
    raise ValueError(f"unsupported profile backend: {backend}")


def _build_vectors(cfg: AgentKitConfig) -> VectorStore:
    backend = cfg.stores.vector_backend
    if backend is StoreBackend.MEMORY:
        return InMemoryVectorStore()
    if backend is StoreBackend.QDRANT:
        from agent_kit.stores.qdrant_vectors import QdrantVectorStore
        qcfg = cfg.stores.qdrant
        return QdrantVectorStore(
            mode=qcfg.mode,
            path=qcfg.path,
            url=qcfg.url,
            collection=qcfg.collection,
            vector_size=qcfg.vector_size,
        )
    raise ValueError(f"unsupported vector backend: {backend}")


def _build_permissions(cfg: AgentKitConfig) -> PermissionStore:
    backend = cfg.stores.permission_backend
    default = set(cfg.tools.default_allowed)
    if backend is StoreBackend.MEMORY:
        return InMemoryPermissionStore(default_allowed=default)
    if backend is StoreBackend.SQLITE:
        from agent_kit.stores.sqlite_permissions import SqlitePermissionStore
        return SqlitePermissionStore(cfg.stores.sqlite.url, default)
    raise ValueError(f"unsupported permission backend: {backend}")
