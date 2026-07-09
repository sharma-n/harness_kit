"""Persistence adapters, each behind a Protocol (SPEC §4.2)."""

from agent_kit.stores.base import (
    PermissionStore,
    ProfileStore,
    SessionStore,
    SkillStore,
    VectorStore,
)
from agent_kit.stores.factory import Stores, build_stores
from agent_kit.stores.memory_permissions import InMemoryPermissionStore
from agent_kit.stores.memory_profile import InMemoryProfileStore
from agent_kit.stores.memory_session import InMemorySessionStore
from agent_kit.stores.memory_skills import InMemorySkillStore
from agent_kit.stores.memory_vectors import InMemoryVectorStore
from agent_kit.stores.types import (
    MemoryHit,
    MemoryPoint,
    SessionState,
    Turn,
    UserProfile,
)

__all__ = [
    "InMemoryPermissionStore",
    "InMemoryProfileStore",
    "InMemorySessionStore",
    "InMemorySkillStore",
    "InMemoryVectorStore",
    "MemoryHit",
    "MemoryPoint",
    "PermissionStore",
    "ProfileStore",
    "SessionState",
    "SessionStore",
    "SkillStore",
    "Stores",
    "Turn",
    "UserProfile",
    "VectorStore",
    "build_stores",
]
