"""Serving layer: ws + SSE drive a turn end-to-end against FakeLLM (SPEC §15)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from agent_kit.config import AgentKitConfig
from agent_kit.serving.app import create_app

from tests.conftest import ScriptedTurn, make_service, tc


def _client(turns, *, extra_tools=None, default_allowed=None) -> TestClient:
    cfg = AgentKitConfig()
    if default_allowed is not None:
        cfg.tools.default_allowed = default_allowed
    service, _ = make_service(cfg, turns=turns, extra_tools=extra_tools)
    return TestClient(create_app(service))


def test_healthz():
    client = _client([ScriptedTurn(text_chunks=["hi"])])
    assert client.get("/healthz").json() == {"status": "ok"}


def test_websocket_streams_text_and_completes():
    client = _client([ScriptedTurn(text_chunks=["Hello", " world"])])
    with client.websocket_connect("/ws/conv1") as ws:
        ws.send_json({"user_id": "alice", "message": "hi"})
        frames = []
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame["type"] == "turn_complete":
                break
    texts = [f["text"] for f in frames if f["type"] == "text"]
    assert "".join(texts) == "Hello world"
    assert frames[-1]["stop_reason"] == "completed"


def test_sse_streams_frames():
    client = _client([ScriptedTurn(text_chunks=["abc"])])
    with client.stream(
        "GET", "/sse/conv1", params={"user_id": "alice", "message": "hi"}
    ) as resp:
        body = "".join(chunk for chunk in resp.iter_text())
    assert '"type": "text"' in body
    assert '"abc"' in body
    assert '"turn_complete"' in body


def test_websocket_reports_unauthorized_for_cross_user_conversation():
    client = _client([ScriptedTurn(text_chunks=["one"]), ScriptedTurn(text_chunks=["two"])])
    with client.websocket_connect("/ws/conv1") as ws:
        # alice creates + owns conv1
        ws.send_json({"user_id": "alice", "message": "hi"})
        while ws.receive_json()["type"] != "turn_complete":
            pass
        # bob tries to use alice's conversation
        ws.send_json({"user_id": "bob", "message": "intrude"})
        frame = ws.receive_json()
        assert frame["type"] == "error"
        assert "not owned" in frame["error"]
