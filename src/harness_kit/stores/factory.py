"""Store assembly — the single place backends are selected from config.

``build_stores(cfg)`` returns a ``Stores`` bundle of the four Protocol-typed
adapters. Defaults to in-memory; flipping a ``*_backend`` in config swaps in a
real adapter with no change to any layer above ``stores/``.
"""

from __future__ import annotations

from dataclasses import dataclass

from harness_kit.config import HarnessKitConfig, StoreBackend
from harness_kit.stores.base import (
    PermissionStore,
    ProfileStore,
    SessionStore,
    SkillStore,
    VectorStore,
)
from harness_kit.stores.memory_permissions import InMemoryPermissionStore
from harness_kit.stores.memory_profile import InMemoryProfileStore
from harness_kit.stores.memory_session import InMemorySessionStore
from harness_kit.stores.memory_skills import InMemorySkillStore
from harness_kit.stores.memory_vectors import InMemoryVectorStore


@dataclass(slots=True)
class Stores:
    session: SessionStore
    profile: ProfileStore
    vectors: VectorStore
    permissions: PermissionStore
    skills: SkillStore


def build_stores(
    cfg: HarnessKitConfig,
    extra_default_allowed: set[str] | None = None,
) -> Stores:
    return Stores(
        session=_build_session(cfg),
        profile=_build_profile(cfg),
        vectors=_build_vectors(cfg),
        permissions=_build_permissions(cfg, extra_default_allowed),
        skills=_build_skills(),
    )


def _build_session(cfg: HarnessKitConfig) -> SessionStore:
    backend = cfg.stores.session_backend
    if backend is StoreBackend.MEMORY:
        return InMemorySessionStore(ttl_s=cfg.memory.working.ttl_s)
    if backend is StoreBackend.REDIS:
        from harness_kit.stores.redis_session import RedisSessionStore
        return RedisSessionStore(cfg.stores.redis.url, ttl_s=cfg.memory.working.ttl_s)
    raise ValueError(f"unsupported session backend: {backend}")


def _build_profile(cfg: HarnessKitConfig) -> ProfileStore:
    backend = cfg.stores.profile_backend
    if backend is StoreBackend.MEMORY:
        return InMemoryProfileStore()
    if backend is StoreBackend.SQLITE:
        from harness_kit.stores.sqlite_profile import SqliteProfileStore
        return SqliteProfileStore(cfg.stores.sqlite.url)
    raise ValueError(f"unsupported profile backend: {backend}")


def _build_vectors(cfg: HarnessKitConfig) -> VectorStore:
    backend = cfg.stores.vector_backend
    if backend is StoreBackend.MEMORY:
        return InMemoryVectorStore()
    if backend is StoreBackend.QDRANT:
        from harness_kit.stores.qdrant_vectors import QdrantVectorStore
        qcfg = cfg.stores.qdrant
        return QdrantVectorStore(
            mode=qcfg.mode,
            path=qcfg.path,
            url=qcfg.url,
            collection=qcfg.collection,
            vector_size=qcfg.vector_size,
        )
    raise ValueError(f"unsupported vector backend: {backend}")


def _build_permissions(
    cfg: HarnessKitConfig,
    extra_default_allowed: set[str] | None = None,
) -> PermissionStore:
    backend = cfg.stores.permission_backend
    default = set(cfg.tools.default_allowed)
    if extra_default_allowed:
        default = default | extra_default_allowed
    if backend is StoreBackend.MEMORY:
        return InMemoryPermissionStore(default_allowed=default)
    if backend is StoreBackend.SQLITE:
        from harness_kit.stores.sqlite_permissions import SqlitePermissionStore
        return SqlitePermissionStore(cfg.stores.sqlite.url, default)
    raise ValueError(f"unsupported permission backend: {backend}")


def _build_skills() -> SkillStore:
    return InMemorySkillStore()
