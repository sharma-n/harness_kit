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

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from agent_kit.config import AgentKitConfig
from agent_kit.errors import UnauthorizedError
from agent_kit.service import AgentService
from agent_kit.serving.wire import encode_event

logger = logging.getLogger(__name__)


def create_app(service: AgentService) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Background idle sweeper: finalizes conversations that have gone idle past
        # ``idle_finalize_s``. This is the transport-agnostic conversation-end signal
        # — SSE never disconnects and WebSockets can drop without firing their handler.
        sweeper = asyncio.create_task(_idle_sweep_loop(service))
        try:
            yield
        finally:
            sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper

    app = FastAPI(title="agent_kit", lifespan=lifespan)

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
        last_user_id: str | None = None
        try:
            while True:
                raw = await websocket.receive_text()
                payload = json.loads(raw)
                user_id = payload["user_id"]
                last_user_id = user_id
                message = payload["message"]
                try:
                    async for event in service.agent.run_turn(
                        user_id, conversation_id, message
                    ):
                        await websocket.send_json(encode_event(event))
                except UnauthorizedError as exc:
                    await websocket.send_json({"type": "error", "error": str(exc)})
        except WebSocketDisconnect:
            # Conversation ended → embed it as one episodic point (off the hot path).
            # Log a finalize failure here rather than let it surface as an unhandled
            # task error; the idle sweeper is the backstop if this disconnect path fails.
            if last_user_id is not None:
                try:
                    await service.agent.end_conversation(last_user_id, conversation_id)
                except Exception:
                    logger.exception(
                        "conversation finalize on disconnect failed "
                        "(user_id=%s conversation_id=%s)",
                        last_user_id,
                        conversation_id,
                    )

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


async def _idle_sweep_loop(service: AgentService) -> None:
    """Periodically finalize idle conversations until the app shuts down."""
    cfg = service.cfg.memory.working
    while True:
        await asyncio.sleep(cfg.sweep_interval_s)
        try:
            await service.agent.sweep_idle(cfg.idle_finalize_s)
        except Exception:  # never let a sweep failure kill the loop
            logger.exception("idle sweep failed")


def create_app_from_yaml(path: str = "config.yaml") -> FastAPI:
    """ASGI entrypoint factory: ``uvicorn agent_kit.serving.app:app``-friendly."""
    return create_app(AgentService.from_yaml(path))
