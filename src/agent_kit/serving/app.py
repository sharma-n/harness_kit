"""FastAPI serving layer (SPEC §10).

Both transports consume the same ``AgentEvent`` stream and forward ``TextDelta``s
as they arrive:
  - WebSocket ``/ws/{conversation_id}`` — bidirectional; client sends JSON
    ``{"user_id", "message"}`` turns, receives encoded event frames.
  - SSE ``/sse/{conversation_id}`` — one turn per request (``user_id``/``message``
    as query params), server-streamed event frames.

Auth is a stub: ``user_id`` comes from the client. A real deployment resolves it
from a verified token — the rest of the stack already treats ``user_id`` as the
isolation key, so only this resolver changes.

Backpressure: each send is awaited, so a slow client paces its own stream without
stalling the shared event loop (other connections run as independent tasks).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from agent_kit.config import AgentKitConfig
from agent_kit.errors import UnauthorizedError
from agent_kit.service import AgentService
from agent_kit.serving.wire import encode_event


def create_app(service: AgentService) -> FastAPI:
    app = FastAPI(title="agent_kit")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics() -> dict[str, str]:
        # Placeholder; observability lands in a later milestone (SPEC §13).
        return {"status": "not_implemented"}

    @app.websocket("/ws/{conversation_id}")
    async def ws(websocket: WebSocket, conversation_id: str) -> None:
        await websocket.accept()
        try:
            while True:
                raw = await websocket.receive_text()
                payload = json.loads(raw)
                user_id = payload["user_id"]
                message = payload["message"]
                try:
                    async for event in service.agent.run_turn(
                        user_id, conversation_id, message
                    ):
                        await websocket.send_json(encode_event(event))
                except UnauthorizedError as exc:
                    await websocket.send_json({"type": "error", "error": str(exc)})
        except WebSocketDisconnect:
            return

    @app.get("/sse/{conversation_id}")
    async def sse(
        conversation_id: str,
        user_id: str = Query(...),
        message: str = Query(...),
    ) -> StreamingResponse:
        async def stream() -> AsyncIterator[bytes]:
            try:
                async for event in service.agent.run_turn(
                    user_id, conversation_id, message
                ):
                    yield _sse_frame(encode_event(event))
            except UnauthorizedError as exc:
                yield _sse_frame({"type": "error", "error": str(exc)})

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


def _sse_frame(data: dict) -> bytes:
    return f"data: {json.dumps(data)}\n\n".encode()


def create_app_from_yaml(path: str = "config.yaml") -> FastAPI:
    """ASGI entrypoint factory: ``uvicorn agent_kit.serving.app:app``-friendly."""
    return create_app(AgentService.from_yaml(path))
