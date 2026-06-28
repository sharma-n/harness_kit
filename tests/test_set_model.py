"""Per-conversation model tracking (set_conversation_model / llm_factory)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_kit.config import AgentKitConfig
from agent_kit.serving.app import create_app
from agent_kit.serving.wire import encode_conversation
from agent_kit.stores.types import ConversationMeta, SessionState

from tests.conftest import FakeLLM, ScriptedTurn, make_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_override_service(default_turns=None, override_turns=None):
    """Build a service with a factory spy patched in.

    Returns (service, default_llm, override_llm, factory_calls).
    ``factory_calls`` is a list that accumulates the model names passed to the spy.
    """
    cfg = AgentKitConfig()
    service, default_llm = make_service(
        cfg, turns=default_turns or [ScriptedTurn(text_chunks=["default"])]
    )
    override_llm = FakeLLM(turns=override_turns or [ScriptedTurn(text_chunks=["override"])])
    factory_calls: list[str] = []

    def spy_factory(model_name: str) -> FakeLLM:
        factory_calls.append(model_name)
        return override_llm

    service._llm_factory = spy_factory
    service.agent._llm_factory = spy_factory
    return service, default_llm, override_llm, factory_calls


# ---------------------------------------------------------------------------
# Unit: store round-trip via WorkingMemory.get_model_name
# ---------------------------------------------------------------------------

async def test_get_model_name_reads_from_session(base_config):
    service, _ = make_service(base_config)
    state = SessionState(user_id="alice", model_name="claude-opus-4-8")
    await service.stores.session.save("conv1", state)

    result = await service.agent._working.get_model_name("conv1", "alice")
    assert result == "claude-opus-4-8"


async def test_get_model_name_returns_none_when_unset(base_config):
    service, _ = make_service(base_config)
    state = SessionState(user_id="alice")
    await service.stores.session.save("conv1", state)

    result = await service.agent._working.get_model_name("conv1", "alice")
    assert result is None


async def test_get_model_name_returns_none_for_missing_session(base_config):
    service, _ = make_service(base_config)
    result = await service.agent._working.get_model_name("no-such-conv", "alice")
    assert result is None


# ---------------------------------------------------------------------------
# Unit: set_conversation_model
# ---------------------------------------------------------------------------

async def test_set_conversation_model_raises_when_factory_unavailable(base_config):
    service, _ = make_service(base_config)
    # make_service injects FakeLLM → _llm_factory is None
    assert service._llm_factory is None
    with pytest.raises(ValueError, match="externally injected"):
        await service.set_conversation_model("conv1", "alice", "claude-opus-4-8")


async def test_set_conversation_model_persists_model_name():
    service, *_ = _make_override_service()

    await service.set_conversation_model("conv1", "alice", "claude-opus-4-8")

    state = await service.stores.session.load("conv1", "alice")
    assert state is not None
    assert state.model_name == "claude-opus-4-8"


async def test_set_conversation_model_clears_with_none():
    service, *_ = _make_override_service()

    await service.set_conversation_model("conv1", "alice", "claude-opus-4-8")
    await service.set_conversation_model("conv1", "alice", None)

    state = await service.stores.session.load("conv1", "alice")
    assert state.model_name is None


# ---------------------------------------------------------------------------
# Integration: run_turn LLM resolution
# ---------------------------------------------------------------------------

async def test_run_turn_uses_override_model_from_factory():
    service, default_llm, override_llm, factory_calls = _make_override_service()

    # Seed model_name directly in the session store (bypasses ValueError guard)
    state = SessionState(user_id="alice", model_name="claude-opus-4-8")
    await service.stores.session.save("conv1", state)

    events = [e async for e in service.agent.run_turn("alice", "conv1", "hi")]
    await service.agent.drain()

    assert factory_calls == ["claude-opus-4-8"]
    assert len(override_llm.stream_calls) == 1
    assert len(default_llm.stream_calls) == 0


async def test_run_turn_uses_default_when_no_override():
    service, default_llm, override_llm, factory_calls = _make_override_service(
        default_turns=[ScriptedTurn(text_chunks=["default answer"])]
    )
    # No model_name set in the session

    events = [e async for e in service.agent.run_turn("alice", "conv1", "hi")]
    await service.agent.drain()

    assert factory_calls == []
    assert len(default_llm.stream_calls) == 1
    assert len(override_llm.stream_calls) == 0


async def test_run_turn_factory_called_once_per_turn_not_cached_here():
    """Factory is called each turn; caching happens inside the factory closure."""
    service, _, override_llm, factory_calls = _make_override_service(
        override_turns=[
            ScriptedTurn(text_chunks=["first"]),
            ScriptedTurn(text_chunks=["second"]),
        ]
    )
    state = SessionState(user_id="alice", model_name="claude-opus-4-8")
    await service.stores.session.save("conv1", state)

    [e async for e in service.agent.run_turn("alice", "conv1", "turn 1")]
    await service.agent.drain()
    [e async for e in service.agent.run_turn("alice", "conv1", "turn 2")]
    await service.agent.drain()

    assert factory_calls == ["claude-opus-4-8", "claude-opus-4-8"]


# ---------------------------------------------------------------------------
# Serving: WebSocket set_model message
# ---------------------------------------------------------------------------

def test_ws_set_model_message_stores_model_name():
    service, _, _, _ = _make_override_service(
        override_turns=[ScriptedTurn(text_chunks=["hi"])]
    )
    client = TestClient(create_app(service))

    with client.websocket_connect("/ws/conv1") as ws:
        ws.send_json({"type": "set_model", "user_id": "alice", "model": "claude-opus-4-8"})
        ws.send_json({"user_id": "alice", "message": "hello"})
        while ws.receive_json()["type"] != "turn_complete":
            pass

    # Session should now have the model_name stored
    store = service.stores.session
    state = store._sessions.get("conv1")
    assert state is not None
    assert state.model_name == "claude-opus-4-8"


def test_ws_set_model_message_with_null_clears_override():
    service, default_llm, _, _ = _make_override_service(
        default_turns=[ScriptedTurn(text_chunks=["hi"])]
    )
    client = TestClient(create_app(service))

    with client.websocket_connect("/ws/conv1") as ws:
        ws.send_json({"type": "set_model", "user_id": "alice", "model": "claude-opus-4-8"})
        ws.send_json({"type": "set_model", "user_id": "alice", "model": None})
        ws.send_json({"user_id": "alice", "message": "hello"})
        while ws.receive_json()["type"] != "turn_complete":
            pass

    state = service.stores.session._sessions.get("conv1")
    assert state is not None
    assert state.model_name is None


# ---------------------------------------------------------------------------
# Serving: REST PUT /conversations/{id}/model
# ---------------------------------------------------------------------------

def test_rest_set_model_endpoint_stores_model_name():
    service, *_ = _make_override_service()
    client = TestClient(create_app(service))

    resp = client.put(
        "/conversations/conv1/model",
        params={"user_id": "alice", "model": "claude-opus-4-8"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"conversation_id": "conv1", "model": "claude-opus-4-8"}

    state = service.stores.session._sessions.get("conv1")
    assert state is not None
    assert state.model_name == "claude-opus-4-8"


def test_rest_set_model_endpoint_clears_with_no_model_param():
    service, *_ = _make_override_service()
    client = TestClient(create_app(service))

    # Set then clear
    client.put("/conversations/conv1/model",
               params={"user_id": "alice", "model": "claude-opus-4-8"})
    resp = client.put("/conversations/conv1/model", params={"user_id": "alice"})

    assert resp.status_code == 200
    assert resp.json()["model"] is None


# ---------------------------------------------------------------------------
# Wire: encode_conversation includes model_name
# ---------------------------------------------------------------------------

def test_encode_conversation_includes_model_name():
    meta = ConversationMeta(
        conversation_id="c1",
        user_id="alice",
        created_at=0.0,
        updated_at=1.0,
        finalized_at=None,
        turn_count=3,
        summary_preview="some summary",
        model_name="claude-opus-4-8",
    )
    wire = encode_conversation(meta)
    assert wire["model_name"] == "claude-opus-4-8"


def test_encode_conversation_model_name_none_when_unset():
    meta = ConversationMeta(
        conversation_id="c1",
        user_id="alice",
        created_at=0.0,
        updated_at=1.0,
        finalized_at=None,
        turn_count=0,
        summary_preview="",
    )
    wire = encode_conversation(meta)
    assert wire["model_name"] is None


# ---------------------------------------------------------------------------
# Store: model_name round-trips through ConversationMeta.list()
# ---------------------------------------------------------------------------

async def test_list_returns_model_name_in_meta(base_config):
    service, _ = make_service(base_config)
    state = SessionState(user_id="alice", model_name="claude-haiku-4-5")
    await service.stores.session.save("conv1", state)

    metas = await service.stores.session.list("alice")
    assert len(metas) == 1
    assert metas[0].model_name == "claude-haiku-4-5"
