"""Contract tests for the real store backends.

Strategy:
  - Qdrant   — mode="memory" (in-process AsyncQdrantClient)  → always runs
  - SQLite   — tmp_path file (avoids SQLAlchemy multi-conn in-memory issue) → always runs
  - Redis    — localhost:6379 db=15 → skipped if port unreachable

The same invariants as test_stores.py are verified; these tests guarantee the
real adapters honour the Protocol contracts end-to-end.
"""

from __future__ import annotations

import asyncio
import socket
import time

import pytest

from agent_kit.errors import UnauthorizedError
from agent_kit.stores.qdrant_vectors import QdrantVectorStore
from agent_kit.stores.redis_session import RedisSessionStore
from agent_kit.stores.sqlite_permissions import SqlitePermissionStore
from agent_kit.stores.sqlite_profile import SqliteProfileStore
from agent_kit.stores.types import MemoryPoint, SessionState, Turn


# ---------------------------------------------------------------------------
# Redis availability check (synchronous socket probe — no async needed)
# ---------------------------------------------------------------------------

def _redis_reachable() -> bool:
    try:
        s = socket.socket()
        s.settimeout(0.5)
        s.connect(("localhost", 6379))
        s.close()
        return True
    except OSError:
        return False


_REDIS_URL = "redis://localhost:6379/15"  # db=15 isolated from any real data
requires_redis = pytest.mark.skipif(
    not _redis_reachable(), reason="Redis not reachable at localhost:6379"
)


# ---------------------------------------------------------------------------
# SQLite fixtures (function-scoped temp files)
# ---------------------------------------------------------------------------

@pytest.fixture
async def profile_store(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    store = SqliteProfileStore(url)
    yield store
    await store._engine.dispose()


@pytest.fixture
async def permissions_store(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/test_perms.db"
    store = SqlitePermissionStore(url, default_allowed={"remember_fact"})
    yield store
    await store._engine.dispose()


# ---------------------------------------------------------------------------
# Qdrant fixture (in-process memory client)
# ---------------------------------------------------------------------------

@pytest.fixture
async def qdrant_store():
    store = QdrantVectorStore(mode="memory", collection="test_episodic", vector_size=2)
    yield store
    await store._client.close()


# ---------------------------------------------------------------------------
# Redis fixture (flushes db=15 in teardown)
# ---------------------------------------------------------------------------

@pytest.fixture
async def redis_store():
    import redis.asyncio as aioredis
    store = RedisSessionStore(_REDIS_URL, ttl_s=3600)
    yield store
    # Clean up all keys written during the test.
    await store._client.flushdb()
    await store._client.aclose()


# ---------------------------------------------------------------------------
# SQLite ProfileStore tests
# ---------------------------------------------------------------------------

async def test_sqlite_profile_upsert_merges(profile_store):
    await profile_store.upsert_facts("alice", {"seat": "aisle"})
    await profile_store.upsert_facts("alice", {"tz": "PST"})
    profile = await profile_store.get("alice")
    assert profile.facts == {"seat": "aisle", "tz": "PST"}


async def test_sqlite_profile_get_creates_if_absent(profile_store):
    profile = await profile_store.get("newuser")
    assert profile.user_id == "newuser"
    assert profile.facts == {}


async def test_sqlite_profile_forget_removes_and_ignores_missing(profile_store):
    await profile_store.upsert_facts("alice", {"seat": "aisle", "tz": "PST"})
    await profile_store.forget_facts("alice", {"seat", "nonexistent"})
    profile = await profile_store.get("alice")
    assert profile.facts == {"tz": "PST"}
    # Unknown user is a silent no-op.
    await profile_store.forget_facts("bob", {"seat"})


# ---------------------------------------------------------------------------
# SQLite PermissionStore tests
# ---------------------------------------------------------------------------

async def test_sqlite_permission_default_fallback(permissions_store):
    assert await permissions_store.allowed_tools("newuser") == {"remember_fact"}


async def test_sqlite_permission_grant_and_revoke(permissions_store):
    await permissions_store.grant("alice", {"search_web"})
    assert await permissions_store.allowed_tools("alice") == {"remember_fact", "search_web"}
    await permissions_store.revoke("alice", {"remember_fact"})
    assert await permissions_store.allowed_tools("alice") == {"search_web"}
    # Other users are unaffected.
    assert await permissions_store.allowed_tools("bob") == {"remember_fact"}


async def test_sqlite_permission_extend_default(permissions_store):
    await permissions_store.extend_default_allowed({"extra_tool"})
    assert "extra_tool" in await permissions_store.allowed_tools("newuser")


# ---------------------------------------------------------------------------
# Qdrant VectorStore tests
# ---------------------------------------------------------------------------

async def test_qdrant_search_is_user_isolated(qdrant_store):
    await qdrant_store.add([
        MemoryPoint("p1", [1.0, 0.0], {"user_id": "alice", "text": "a"}),
        MemoryPoint("p2", [1.0, 0.0], {"user_id": "bob", "text": "b"}),
    ])
    hits = await qdrant_store.search("alice", [1.0, 0.0], k=5, min_score=0.0)
    assert len(hits) == 1
    assert hits[0].point.payload["user_id"] == "alice"
    assert hits[0].point.id == "p1"


async def test_qdrant_min_score_threshold(qdrant_store):
    await qdrant_store.add([MemoryPoint("p1", [1.0, 0.0], {"user_id": "alice"})])
    # Orthogonal query → cosine ≈ 0, below threshold → no results.
    hits = await qdrant_store.search("alice", [0.0, 1.0], k=5, min_score=0.5)
    assert hits == []


async def test_qdrant_upsert_overwrites(qdrant_store):
    await qdrant_store.add([MemoryPoint("p1", [1.0, 0.0], {"user_id": "alice", "v": 1})])
    await qdrant_store.add([MemoryPoint("p1", [1.0, 0.0], {"user_id": "alice", "v": 2})])
    hits = await qdrant_store.search("alice", [1.0, 0.0], k=5, min_score=0.0)
    assert len(hits) == 1
    assert hits[0].point.payload.get("v") == 2


async def test_qdrant_id_roundtrip(qdrant_store):
    await qdrant_store.add([MemoryPoint("conv:my-conv-123", [1.0, 0.0], {"user_id": "alice"})])
    hits = await qdrant_store.search("alice", [1.0, 0.0], k=5, min_score=0.0)
    assert hits[0].point.id == "conv:my-conv-123"


# ---------------------------------------------------------------------------
# Redis SessionStore tests
# ---------------------------------------------------------------------------

@requires_redis
async def test_redis_session_roundtrip(redis_store):
    await redis_store.save("c1", SessionState(user_id="alice"))
    await redis_store.append_turn("c1", Turn(role="user", text="hello"))
    state = await redis_store.load("c1", "alice")
    assert state is not None
    assert [t.text for t in state.working_buffer] == ["hello"]


@requires_redis
async def test_redis_session_ownership_blocks_cross_user(redis_store):
    await redis_store.save("c1", SessionState(user_id="alice"))
    with pytest.raises(UnauthorizedError):
        await redis_store.load("c1", "bob")


@requires_redis
async def test_redis_session_load_missing_returns_none(redis_store):
    assert await redis_store.load("nonexistent", "alice") is None


@requires_redis
async def test_redis_session_due_for_finalize(redis_store):
    await redis_store.save("c1", SessionState(user_id="alice"))
    # Back-date the pending_finalize ZSET score to simulate 100s of idle.
    past = time.time() - 100
    await redis_store._client.zadd("sessions:pending_finalize", {"c1": past}, xx=True)
    due = await redis_store.due_for_finalize(idle_s=50)
    assert any(conv_id == "c1" for conv_id, _ in due)


@requires_redis
async def test_redis_session_mark_finalized(redis_store):
    await redis_store.save("c1", SessionState(user_id="alice"))
    await redis_store.mark_finalized("c1")
    state = await redis_store.load("c1", "alice")
    assert state is not None
    assert state.finalized_at is not None
    # Should no longer be in pending_finalize ZSET.
    score = await redis_store._client.zscore("sessions:pending_finalize", "c1")
    assert score is None


@requires_redis
async def test_redis_session_list_newest_first(redis_store):
    await redis_store.save("c1", SessionState(user_id="alice"))
    await asyncio.sleep(0.01)
    await redis_store.save("c2", SessionState(user_id="alice"))
    metas = await redis_store.list("alice")
    assert [m.conversation_id for m in metas] == ["c2", "c1"]


@requires_redis
async def test_redis_session_list_user_isolated(redis_store):
    await redis_store.save("c1", SessionState(user_id="alice"))
    await redis_store.save("c2", SessionState(user_id="bob"))
    metas = await redis_store.list("alice")
    assert all(m.user_id == "alice" for m in metas)
    assert len(metas) == 1
