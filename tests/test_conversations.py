"""Conversation listing & metadata (M11): store ``list`` + ``GET /conversations``."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_kit.config import AgentKitConfig
from agent_kit.serving.app import create_app
from agent_kit.stores.memory_session import InMemorySessionStore
from agent_kit.stores.types import SessionState, Turn

from tests.conftest import ScriptedTurn, make_service

# --------------------------------------------------------------------------- #
# Store level
# --------------------------------------------------------------------------- #


async def test_list_returns_user_metadata_newest_first():
    store = InMemorySessionStore()
    a1 = SessionState(user_id="alice", working_buffer=[Turn("user", "hi")], rolling_summary="s1")
    a2 = SessionState(
        user_id="alice",
        working_buffer=[Turn("user", "a"), Turn("assistant", "b")],
        rolling_summary="s2",
    )
    b1 = SessionState(user_id="bob")
    await store.save("c1", a1)
    await store.save("c2", a2)
    await store.save("c3", b1)
    # save() pins updated_at to now; override for deterministic ordering.
    a1.updated_at, a2.updated_at = 100.0, 200.0

    metas = await store.list("alice")

    assert [m.conversation_id for m in metas] == ["c2", "c1"]  # newest-first
    assert metas[0].turn_count == 2
    assert metas[0].summary_preview == "s2"
    assert metas[1].turn_count == 1
    # bob's conversation never leaks into alice's listing.
    assert all(m.user_id == "alice" for m in metas)


async def test_list_summary_preview_is_truncated():
    store = InMemorySessionStore()
    await store.save("c1", SessionState(user_id="alice", rolling_summary="x" * 500))

    metas = await store.list("alice")

    assert len(metas[0].summary_preview) == 200


async def test_list_skips_ttl_expired_sessions_without_evicting():
    store = InMemorySessionStore(ttl_s=1)
    state = SessionState(user_id="alice")
    await store.save("c1", state)
    state.updated_at = 0.0  # far in the past → expired

    assert await store.list("alice") == []
    # read-only: the expired session is still present (load/sweeper evict, not list).
    assert "c1" in store._sessions


# --------------------------------------------------------------------------- #
# Serving level
# --------------------------------------------------------------------------- #


def _client(turns) -> TestClient:
    service, _ = make_service(AgentKitConfig(), turns=turns)
    return TestClient(create_app(service))


def _drive_sse(client: TestClient, user_id: str, conv: str) -> None:
    """Run one turn over SSE — no disconnect, so the conversation stays active."""
    with client.stream(
        "GET", f"/sse/{conv}", params={"user_id": user_id, "message": "hi"}
    ) as resp:
        for _ in resp.iter_text():
            pass


def _drive_ws(client: TestClient, user_id: str, conv: str) -> None:
    """Run one turn over WS; the context exit disconnects → conversation finalized."""
    with client.websocket_connect(f"/ws/{conv}") as ws:
        ws.send_json({"user_id": user_id, "message": "hi"})
        while ws.receive_json()["type"] != "turn_complete":
            pass


def test_list_conversations_endpoint_returns_metadata():
    client = _client([ScriptedTurn(text_chunks=["hello"])])
    _drive_sse(client, "alice", "conv1")

    convs = client.get("/conversations", params={"user_id": "alice"}).json()["conversations"]

    assert len(convs) == 1
    c = convs[0]
    assert c["conversation_id"] == "conv1"
    assert c["turn_count"] == 2  # user + assistant
    assert c["finalized_at"] is None
    assert {"created_at", "updated_at", "summary_preview"} <= c.keys()


def test_list_conversations_isolation_status_and_limit():
    client = _client([ScriptedTurn(["a"]), ScriptedTurn(["b"]), ScriptedTurn(["c"])])
    _drive_sse(client, "alice", "active1")  # active
    _drive_ws(client, "alice", "done1")     # finalized on disconnect
    _drive_sse(client, "bob", "bob1")       # different user

    alice = client.get("/conversations", params={"user_id": "alice"}).json()["conversations"]
    assert {c["conversation_id"] for c in alice} == {"active1", "done1"}  # bob excluded

    active = client.get(
        "/conversations", params={"user_id": "alice", "status": "active"}
    ).json()["conversations"]
    assert [c["conversation_id"] for c in active] == ["active1"]

    finalized = client.get(
        "/conversations", params={"user_id": "alice", "status": "finalized"}
    ).json()["conversations"]
    assert [c["conversation_id"] for c in finalized] == ["done1"]

    limited = client.get(
        "/conversations", params={"user_id": "alice", "limit": 1}
    ).json()["conversations"]
    assert len(limited) == 1
