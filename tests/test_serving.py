"""Serving layer: ws + SSE drive a turn end-to-end against FakeLLM (SPEC §15)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from harness_kit.config import HarnessKitConfig, ToolPolicy
from harness_kit.serving.app import create_app
from harness_kit.tools.base import Tool
from llm_kit import ToolDefinition

from tests.conftest import ScriptedTurn, make_service, tc


def _client(turns, *, extra_tools=None, default_allowed=None) -> TestClient:
    cfg = HarnessKitConfig()
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


# ---------------------------------------------------------------------------
# HITL approval tests
# ---------------------------------------------------------------------------

def _email_tool_serving(calls: list[str]) -> Tool:
    async def handler(user_id: str, args: dict) -> str:
        calls.append(args.get("to", "?"))
        return f"email sent to {args.get('to')}"

    return Tool(
        ToolDefinition(name="send_email", description="send an email", parameters={}),
        handler,
    )


def _approval_client(turns, *, extra_tools=None, timeout_s: float = 5.0) -> TestClient:
    cfg = HarnessKitConfig()
    cfg.tools.default_allowed = ["send_email"]
    cfg.tools.definitions = {
        "send_email": ToolPolicy(requires_approval=True, approval_timeout_s=timeout_s)
    }
    service, _ = make_service(cfg, turns=turns, extra_tools=extra_tools)
    return TestClient(create_app(service))


def test_ws_approval_approved():
    calls: list[str] = []
    client = _approval_client(
        turns=[
            ScriptedTurn(tool_calls=[tc("call-1", "send_email", to="bob@example.com")]),
            ScriptedTurn(text_chunks=["Email sent!"]),
        ],
        extra_tools=[_email_tool_serving(calls)],
    )
    with client.websocket_connect("/ws/conv1") as ws:
        ws.send_json({"user_id": "alice", "message": "send email to bob"})
        frames = []
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame["type"] == "tool_approval_required":
                ws.send_json({
                    "type": "approval",
                    "call_id": frame["call_id"],
                    "approved": True,
                })
            if frame["type"] == "turn_complete":
                break

    types = [f["type"] for f in frames]
    assert "tool_approval_required" in types
    assert "tool_call" in types  # ToolCallStarted emitted after approval
    tool_results = [f for f in frames if f["type"] == "tool_result"]
    assert len(tool_results) == 1
    assert tool_results[0]["ok"] is True
    assert calls == ["bob@example.com"]  # handler ran


def test_ws_approval_denied():
    calls: list[str] = []
    client = _approval_client(
        turns=[
            ScriptedTurn(tool_calls=[tc("call-1", "send_email", to="bob@example.com")]),
            ScriptedTurn(text_chunks=["Okay, won't send."]),
        ],
        extra_tools=[_email_tool_serving(calls)],
    )
    with client.websocket_connect("/ws/conv1") as ws:
        ws.send_json({"user_id": "alice", "message": "send email to bob"})
        frames = []
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame["type"] == "tool_approval_required":
                ws.send_json({
                    "type": "approval",
                    "call_id": frame["call_id"],
                    "approved": False,
                })
            if frame["type"] == "turn_complete":
                break

    types = [f["type"] for f in frames]
    assert "tool_approval_required" in types
    assert "tool_call" not in types  # ToolCallStarted NOT emitted when denied
    tool_results = [f for f in frames if f["type"] == "tool_result"]
    assert len(tool_results) == 1
    assert tool_results[0]["ok"] is False
    assert "user denied approval" in tool_results[0]["content"]
    assert calls == []  # handler never ran


def test_sse_approval_auto_denied():
    calls: list[str] = []
    client = _approval_client(
        turns=[
            ScriptedTurn(tool_calls=[tc("call-1", "send_email", to="bob@example.com")]),
            ScriptedTurn(text_chunks=["Can't send, approval needed."]),
        ],
        extra_tools=[_email_tool_serving(calls)],
    )
    with client.stream(
        "GET", "/sse/conv1", params={"user_id": "alice", "message": "send email"}
    ) as resp:
        body = "".join(chunk for chunk in resp.iter_text())

    assert '"tool_approval_required"' in body
    assert '"tool_result"' in body
    assert '"ok": false' in body
    assert "denied" in body
    assert calls == []  # handler never ran
