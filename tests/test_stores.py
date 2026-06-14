"""Store contract + multi-user isolation tests (SPEC §15)."""

from __future__ import annotations

import pytest

from agent_kit.errors import UnauthorizedError
from agent_kit.stores.memory_permissions import InMemoryPermissionStore
from agent_kit.stores.memory_profile import InMemoryProfileStore
from agent_kit.stores.memory_session import InMemorySessionStore
from agent_kit.stores.memory_vectors import InMemoryVectorStore
from agent_kit.stores.types import MemoryPoint, SessionState, Turn


async def test_session_roundtrip_and_append():
    store = InMemorySessionStore()
    await store.save("c1", SessionState(user_id="alice"))
    await store.append_turn("c1", Turn(role="user", text="hi"))
    state = await store.load("c1", "alice")
    assert state is not None
    assert [t.text for t in state.working_buffer] == ["hi"]


async def test_session_ownership_blocks_cross_user():
    store = InMemorySessionStore()
    await store.save("c1", SessionState(user_id="alice"))
    with pytest.raises(UnauthorizedError):
        await store.load("c1", "bob")


async def test_session_ttl_expiry():
    store = InMemorySessionStore(ttl_s=0)
    await store.save("c1", SessionState(user_id="alice"))
    # ttl_s=0 means anything older than now is expired.
    assert await store.load("c1", "alice") is None


async def test_profile_upsert_merges():
    store = InMemoryProfileStore()
    await store.upsert_facts("alice", {"seat": "aisle"})
    await store.upsert_facts("alice", {"tz": "PST"})
    profile = await store.get("alice")
    assert profile.facts == {"seat": "aisle", "tz": "PST"}


async def test_vector_search_is_user_isolated():
    store = InMemoryVectorStore()
    await store.add(
        [
            MemoryPoint("p1", [1.0, 0.0], {"user_id": "alice", "text": "a"}),
            MemoryPoint("p2", [1.0, 0.0], {"user_id": "bob", "text": "b"}),
        ]
    )
    hits = await store.search("alice", [1.0, 0.0], k=5, min_score=0.0)
    assert len(hits) == 1
    assert hits[0].point.payload["user_id"] == "alice"


async def test_vector_min_score_threshold():
    store = InMemoryVectorStore()
    await store.add([MemoryPoint("p1", [1.0, 0.0], {"user_id": "alice", "text": "a"})])
    # Orthogonal query → cosine 0, below threshold → nothing injected.
    assert await store.search("alice", [0.0, 1.0], k=5, min_score=0.5) == []


async def test_permissions_default_fallback_then_grant_revoke():
    store = InMemoryPermissionStore(default_allowed={"remember_fact"})
    assert await store.allowed_tools("newuser") == {"remember_fact"}
    await store.grant("alice", {"search_web"})
    assert await store.allowed_tools("alice") == {"remember_fact", "search_web"}
    await store.revoke("alice", {"remember_fact"})
    assert await store.allowed_tools("alice") == {"search_web"}
    # Grant to alice does not leak to other users.
    assert await store.allowed_tools("bob") == {"remember_fact"}
